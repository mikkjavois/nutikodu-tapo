from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
import os
import asyncio
from threading import Thread
import statistics
import logging
from waitress import serve

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration file path
CONFIG_FILE = "config.json"
# Default threshold values
DEFAULT_THRESHOLD_MULTIPLIER = 1.5

# Global state
devices: Dict[str, str] = {}
device_thresholds: Dict[str, dict] = {}  # Per-device threshold config: {type: 'multiplier'|'fixed', value: float}
forced_states: Dict[str, Optional[bool]] = {}  # None = auto, True = force on, False = force off
scheduler = None

def load_config() -> None:
    """Load configuration from file"""
    global devices, device_thresholds, forced_states
    
    if os.path.exists(CONFIG_FILE):
        logger.info(f"Loading configuration from {CONFIG_FILE}")
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                devices = config.get('devices', {})
                device_thresholds_raw = config.get('device_thresholds', {})
                forced_states = config.get('forced_states', {})
                
                device_thresholds = {}
                for name in devices:
                    if name in device_thresholds_raw:
                        threshold_data = device_thresholds_raw[name]
                        if isinstance(threshold_data, dict):
                            device_thresholds[name] = threshold_data
                        else:
                            logger.warning(f"Device {name} has invalid threshold data type, using default (multiplier: {DEFAULT_THRESHOLD_MULTIPLIER})")
                            device_thresholds[name] = {'type': 'multiplier', 'value': DEFAULT_THRESHOLD_MULTIPLIER}
                    else:
                        logger.warning(f"Device {name} missing threshold configuration in config file, using default (multiplier: {DEFAULT_THRESHOLD_MULTIPLIER})")
                        device_thresholds[name] = {'type': 'multiplier', 'value': DEFAULT_THRESHOLD_MULTIPLIER}
                    
                    if name not in forced_states:
                        forced_states[name] = None
            
            logger.info(f"Loaded {len(devices)} devices from configuration")
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            devices = {}
            device_thresholds = {}
            forced_states = {}
    else:
        # Default configuration
        logger.info("No config file found, creating default configuration")
        devices = {}
        device_thresholds = {}
        forced_states = {}
        save_config()

def save_config() -> None:
    """Save configuration to file"""
    config = {
        'devices': devices,
        'device_thresholds': device_thresholds,
        'forced_states': forced_states
    }
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        logger.debug(f"Configuration saved to {CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Error saving config: {e}")

@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/api/devices', methods=['GET'])
def get_devices():
    """Get all devices"""
    device_list = []
    for name, ip in devices.items():
        if name not in device_thresholds:
            logger.warning(f"Device {name} missing threshold configuration, using default (multiplier: {DEFAULT_THRESHOLD_MULTIPLIER})")
            threshold_config = {'type': 'multiplier', 'value': DEFAULT_THRESHOLD_MULTIPLIER}
        else:
            threshold_config = device_thresholds[name]
        
        device_list.append({
            'name': name,
            'ip': ip,
            'threshold_type': threshold_config['type'],
            'threshold_value': threshold_config['value'],
            'forced_state': forced_states.get(name)
        })
    return jsonify({'devices': device_list})

@app.route('/api/devices', methods=['POST'])
def add_device():
    """Add a new device"""
    data = request.json
    name = data.get('name', '').strip()
    ip = data.get('ip', '').strip()
    threshold_type = data.get('threshold_type', 'multiplier')
    threshold_value = data.get('threshold_value', DEFAULT_THRESHOLD_MULTIPLIER)
    
    logger.info(f"Adding device: {name} ({ip})")
    
    if not name or not ip:
        logger.warning(f"Add device failed: missing name or IP")
        return jsonify({'error': 'Name and IP are required'}), 400
    
    if name in devices:
        logger.warning(f"Add device failed: {name} already exists")
        return jsonify({'error': 'Device with this name already exists'}), 400
    
    if threshold_type not in ['multiplier', 'fixed']:
        logger.warning(f"Add device failed: invalid threshold type {threshold_type}")
        return jsonify({'error': 'Threshold type must be "multiplier" or "fixed"'}), 400
    
    try:
        threshold_value = float(threshold_value)
        if threshold_value <= 0:
            logger.warning(f"Add device failed: threshold value must be positive")
            return jsonify({'error': 'Threshold must be positive'}), 400
    except (TypeError, ValueError):
        logger.warning(f"Add device failed: invalid threshold value")
        return jsonify({'error': 'Invalid threshold value'}), 400
    
    # Validate device connection if scheduler is available
    if scheduler:
        try:
            logger.debug(f"Testing connection to {name} at {ip}")
            # Test connection asynchronously
            future = asyncio.run_coroutine_threadsafe(
                scheduler.test_device_connection(ip), 
                scheduler.loop
            )
            # Wait up to 5 seconds for connection test
            connection_ok = future.result(timeout=5)
            if not connection_ok:
                logger.warning(f"Add device failed: cannot connect to {ip}")
                return jsonify({'error': 'Cannot connect to device. Please check IP address and network.'}), 400
            logger.info(f"Connection test successful for {name}")
        except Exception as e:
            logger.error(f"Connection test exception for {name}: {e}")
            return jsonify({'error': f'Connection test failed: {str(e)}'}), 400
    
    devices[name] = ip
    device_thresholds[name] = {'type': threshold_type, 'value': threshold_value}
    forced_states[name] = None
    save_config()
    
    # Trigger price update to calculate timeframes for the new device
    if scheduler:
        logger.info(f"Triggering price update for new device {name}")
        asyncio.run_coroutine_threadsafe(scheduler.update_prices(), scheduler.loop)
        # Immediately check device state
        asyncio.run_coroutine_threadsafe(scheduler.manage_devices(), scheduler.loop)
    
    logger.info(f"Device {name} added successfully")
    return jsonify({'success': True, 'message': f'Device {name} added'})

@app.route('/api/devices/<name>', methods=['PUT'])
def update_device(name):
    """Update device IP and/or threshold"""
    if name not in devices:
        logger.warning(f"Update device failed: {name} not found")
        return jsonify({'error': 'Device not found'}), 404
    
    data = request.json
    new_ip = data.get('ip', '').strip()
    new_threshold_type = data.get('threshold_type')
    new_threshold_value = data.get('threshold_value')
    
    logger.info(f"Updating device: {name}")
    
    if new_ip:
        logger.debug(f"Updating IP for {name}: {new_ip}")
        devices[name] = new_ip
    
    threshold_changed = False
    if new_threshold_type is not None or new_threshold_value is not None:
        # Get current threshold config
        if name not in device_thresholds:
            logger.warning(f"Device {name} missing threshold configuration, using default (multiplier: {DEFAULT_THRESHOLD_MULTIPLIER})")
            current_config = {'type': 'multiplier', 'value': DEFAULT_THRESHOLD_MULTIPLIER}
        else:
            current_config = device_thresholds[name]
        
        # Update type if provided
        if new_threshold_type is not None:
            if new_threshold_type not in ['multiplier', 'fixed']:
                logger.warning(f"Update device failed: invalid threshold type {new_threshold_type}")
                return jsonify({'error': 'Threshold type must be "multiplier" or "fixed"'}), 400
            logger.debug(f"Updating threshold type for {name}: {new_threshold_type}")
            current_config['type'] = new_threshold_type
            threshold_changed = True
        
        # Update value if provided
        if new_threshold_value is not None:
            try:
                new_threshold_value = float(new_threshold_value)
                if new_threshold_value <= 0:
                    logger.warning(f"Update device failed: threshold must be positive")
                    return jsonify({'error': 'Threshold must be positive'}), 400
                logger.debug(f"Updating threshold value for {name}: {new_threshold_value}")
                current_config['value'] = new_threshold_value
                threshold_changed = True
            except (TypeError, ValueError):
                logger.warning(f"Update device failed: invalid threshold value")
                return jsonify({'error': 'Invalid threshold value'}), 400
        
        device_thresholds[name] = current_config
    
    save_config()
    
    # Trigger price update and device state check if threshold changed
    if threshold_changed and scheduler:
        logger.info(f"Threshold changed for {name}, triggering price update")
        asyncio.run_coroutine_threadsafe(scheduler.update_prices(), scheduler.loop)
        # Immediately check device states with new threshold
        asyncio.run_coroutine_threadsafe(scheduler.manage_devices(), scheduler.loop)
    
    logger.info(f"Device {name} updated successfully")
    return jsonify({'success': True, 'message': f'Device {name} updated'})

@app.route('/api/devices/<name>', methods=['DELETE'])
def delete_device(name):
    """Delete a device"""
    if name not in devices:
        logger.warning(f"Delete device failed: {name} not found")
        return jsonify({'error': 'Device not found'}), 404
    
    logger.info(f"Deleting device: {name}")
    del devices[name]
    if name in device_thresholds:
        del device_thresholds[name]
    if name in forced_states:
        del forced_states[name]
    save_config()
    
    logger.info(f"Device {name} deleted successfully")
    return jsonify({'success': True, 'message': f'Device {name} deleted'})

@app.route('/api/devices/<name>/force', methods=['POST'])
def force_device_state(name):
    """Force device on/off or set to auto"""
    if name not in devices:
        logger.warning(f"Force state failed: {name} not found")
        return jsonify({'error': 'Device not found'}), 404
    
    data = request.json
    state = data.get('state')  # 'on', 'off', or 'auto'
    
    logger.info(f"Setting device {name} to {state} mode")
    
    if state == 'on':
        forced_states[name] = True
    elif state == 'off':
        forced_states[name] = False
    elif state == 'auto':
        forced_states[name] = None
    else:
        logger.warning(f"Force state failed: invalid state {state}")
        return jsonify({'error': 'Invalid state. Use "on", "off", or "auto"'}), 400
    
    save_config()
    
    # Trigger immediate device check if scheduler is running
    if scheduler:
        asyncio.run_coroutine_threadsafe(scheduler.manage_devices(), scheduler.loop)
    
    logger.info(f"Device {name} set to {state} successfully")
    return jsonify({'success': True, 'message': f'Device {name} set to {state}'})

@app.route('/api/threshold', methods=['GET'])
def get_threshold():
    """Get threshold multiplier (deprecated - use per-device thresholds)"""
    return jsonify({'threshold_multiplier': DEFAULT_THRESHOLD_MULTIPLIER, 'note': 'Use per-device thresholds instead'})

@app.route('/api/threshold', methods=['POST'])
def update_threshold():
    """Update threshold multiplier (deprecated - use per-device thresholds)"""
    return jsonify({'success': True, 'message': 'Use per-device thresholds instead'})

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get current status of devices and timeframes"""
    status = {
        'current_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'device_states': {},
        'timeframes': {}
    }
    
    if scheduler:
        # Get device states and timeframes per device
        for name, ip in devices.items():
            forced = forced_states.get(name)
            
            if name not in device_thresholds:
                logger.warning(f"Device {name} missing threshold configuration in status check, using default (multiplier: {DEFAULT_THRESHOLD_MULTIPLIER})")
                threshold_config = {'type': 'multiplier', 'value': DEFAULT_THRESHOLD_MULTIPLIER}
            else:
                threshold_config = device_thresholds[name]
            
            # Check device connection status
            try:
                future = asyncio.run_coroutine_threadsafe(
                    scheduler.get_device_state(name, ip),
                    scheduler.loop
                )
                device_state = future.result(timeout=3)
                is_reachable = device_state is not None
            except:
                is_reachable = False
            
            status['device_states'][name] = {
                'forced_state': 'on' if forced is True else 'off' if forced is False else 'auto',
                'threshold_type': threshold_config['type'],
                'threshold_value': threshold_config['value'],
                'should_be_on': scheduler.should_be_on_for_device(name, datetime.now()),
                'is_reachable': is_reachable
            }
            
            # Get timeframes for this device's threshold
            device_timeframes = scheduler.get_timeframes_for_threshold(name)
            status['timeframes'][name] = []
            for start, end, avg, duration in device_timeframes:
                status['timeframes'][name].append({
                    'start': start.strftime('%Y-%m-%d %H:%M'),
                    'end': end.strftime('%Y-%m-%d %H:%M'),
                    'avg_price': round(avg, 2),
                    'duration': duration
                })
    
    return jsonify(status)

@app.route('/api/prices', methods=['GET'])
def get_prices():
    """Get electricity price data (filtered to last 3 hours and future)"""
    if not scheduler or not scheduler.last_prices:
        return jsonify({'prices': [], 'median': 0})
    
    # Filter prices to only show from 3 hours ago onwards
    current_time = datetime.now()
    three_hours_ago = current_time - timedelta(hours=3)
    
    filtered_prices = []
    for price_data in scheduler.last_prices:
        price_time = datetime.fromtimestamp(price_data['timestamp'])
        if price_time >= three_hours_ago:
            filtered_prices.append({
                'timestamp': price_data['timestamp'],
                'time': price_time.strftime('%Y-%m-%d %H:%M'),
                'price': round(price_data['price'], 2)
            })
    
    # Calculate median from filtered prices
    if filtered_prices:
        median = statistics.median([p['price'] for p in filtered_prices])
    else:
        median = 0
    
    return jsonify({
        'prices': filtered_prices,
        'median': round(median, 2),
        'current_time': current_time.timestamp()
    })

def run_flask():
    """Run Flask server"""
    serve(app, host='0.0.0.0', port=5000, threads=2)

def start_web_server():
    """Start Flask server in a separate thread"""
    load_config()
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Web server started at http://localhost:5000")

if __name__ == '__main__':
    load_config()
    serve(app, host='0.0.0.0', port=5000, threads=2)
