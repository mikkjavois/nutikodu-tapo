import asyncio
from tapo import ApiClient, PlugEnergyMonitoringHandler
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import logging
from logging.handlers import RotatingFileHandler
import env
from price_info import fetch_electricity_prices, efficient_timeframes
import web_app

# Configure logging
logger = logging.getLogger(__name__)

class DeviceScheduler:
    def __init__(self) -> None:
        self.price_timeframes: Dict[str, List[Tuple[datetime, datetime, float, int]]] = {}  # Keyed by device name
        self.credentials: Tuple[str, str] = env.CRED
        self.max_retries: int = 3
        self.retry_delay: float = 2.0
        self.loop = None
        self.last_prices = None

    async def get_device(self, ip: str) -> PlugEnergyMonitoringHandler:
        """Get device connection"""
        client = ApiClient(*self.credentials)
        return await client.p110(ip)
    
    def calculate_threshold_price(self, device_name: str, median_price: float) -> float:
        """Calculate the actual threshold price based on device configuration"""
        if device_name not in web_app.device_thresholds:
            logger.warning(f"Device {device_name} has no threshold configuration, using default (multiplier: {web_app.DEFAULT_THRESHOLD_MULTIPLIER})")
            threshold_config = {'type': 'multiplier', 'value': web_app.DEFAULT_THRESHOLD_MULTIPLIER}
        else:
            threshold_config = web_app.device_thresholds[device_name]
        
        if threshold_config['type'] == 'fixed':
            return threshold_config['value']
        else:  # multiplier
            return median_price * threshold_config['value']
    
    async def update_prices(self) -> None:
        """Fetch prices and calculate efficient timeframes for all devices"""
        logger.info("Fetching electricity prices...")
        prices = fetch_electricity_prices()
        if prices:
            self.last_prices = prices
            logger.info(f"Received {len(prices)} price entries")
            
            # Calculate median price from all available prices
            import statistics
            price_values = [p['price'] for p in prices]
            median_price = statistics.median(price_values)
            logger.info(f"Median price: {median_price:.2f} senti/kWh")
            
            # Calculate timeframes for each device individually
            self.price_timeframes = {}
            for device_name in web_app.devices.keys():
                threshold_price = self.calculate_threshold_price(device_name, median_price)
                
                # Pass threshold_price directly to efficient_timeframes
                timeframes = efficient_timeframes(prices, threshold_price)
                self.price_timeframes[device_name] = timeframes
                
                # Get threshold config for logging (already validated in calculate_threshold_price)
                threshold_config = web_app.device_thresholds.get(device_name, {'type': 'multiplier', 'value': web_app.DEFAULT_THRESHOLD_MULTIPLIER})
                if threshold_config['type'] == 'fixed':
                    logger.info(f"Device {device_name} (fixed {threshold_price:.2f} s/kWh): {len(timeframes)} periods found")
                else:
                    logger.info(f"Device {device_name} ({threshold_config['value']:.2f}× median): {len(timeframes)} periods found")
                
                for start, end, avg, duration in timeframes:
                    logger.debug(f"  {start.strftime('%Y-%m-%d %H:%M')} - {end.strftime('%H:%M')} ({duration}min, avg {avg:.2f} senti/kWh)")
        else:
            logger.error("Failed to fetch electricity prices")
    
    def should_be_on_for_device(self, device_name: str, current_time: datetime) -> bool:
        """Check if current time is within any efficient timeframe for a specific device"""
        if current_time.tzinfo is not None:
            current_time = current_time.replace(tzinfo=None)
        
        timeframes = self.price_timeframes.get(device_name, [])
        
        for start, end, _, _ in timeframes:
            if start <= current_time < end:
                return True
        return False
    
    def get_timeframes_for_threshold(self, device_name: str) -> List[Tuple[datetime, datetime, float, int]]:
        """Get timeframes for a specific device"""
        return self.price_timeframes.get(device_name, [])
    
    async def test_device_connection(self, ip: str) -> bool:
        """Test if a device can be reached (for validation)"""
        try:
            device = await self.get_device(ip)
            await device.get_device_info()
            logger.debug(f"Device connection test successful for {ip}")
            return True
        except Exception as e:
            logger.warning(f"Device connection test failed for {ip}: {e}")
            return False
    
    async def get_device_state(self, name: str, ip: str) -> Optional[bool]:
        """Get device state with retry logic"""
        for attempt in range(self.max_retries):
            try:
                device = await self.get_device(ip)
                device_info = await device.get_device_info()
                logger.debug(f"Got state for {name}: {'ON' if device_info.device_on else 'OFF'}")
                return device_info.device_on
            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(f"Error getting {name} state (attempt {attempt + 1}/{self.max_retries}): {e}")
                    logger.info(f"Retrying in {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Failed to get {name} state after {self.max_retries} attempts: {e}")
                    return None
        return None
    
    async def set_device_state(self, name: str, ip: str, turn_on: bool) -> bool:
        """Set device state with retry logic"""
        action_name = "ON" if turn_on else "OFF"
        
        for attempt in range(self.max_retries):
            try:
                device = await self.get_device(ip)
                if turn_on:
                    await device.on()
                else:
                    await device.off()
                logger.info(f"{datetime.now().strftime('%H:%M:%S')} - Turned {action_name} {name}")
                return True
            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(f"Error turning {action_name} {name} (attempt {attempt + 1}/{self.max_retries}): {e}")
                    logger.info(f"Retrying in {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Failed to turn {action_name} {name} after {self.max_retries} attempts: {e}")
                    return False
        return False
    
    async def manage_devices(self) -> None:
        """Check and update device states based on timeframes"""
        current_time: datetime = datetime.now()
        
        devices_snapshot = dict(web_app.devices.items())
        
        for name, ip in devices_snapshot.items():
            # Skip if device was deleted during iteration
            if name not in web_app.devices:
                continue
                
            # Check if device has a forced state
            forced_state = web_app.forced_states.get(name)
            
            if forced_state is not None:
                # Device is in forced mode
                target_state = forced_state
            else:
                # Device is in auto mode - check based on device's own threshold
                target_state = self.should_be_on_for_device(name, current_time)
            
            is_on = await self.get_device_state(name, ip)
            
            if is_on is None:
                continue
            
            if target_state and not is_on:
                await self.set_device_state(name, ip, True)
            elif not target_state and is_on:
                await self.set_device_state(name, ip, False)
    
    async def price_update_loop(self) -> None:
        """Update prices every 4 hours"""
        while True:
            await self.update_prices()
            
            wait_seconds: float = 4 * 3600  # 4 hours in seconds
            logger.info(f"Next price update in 4 hours")
            await asyncio.sleep(wait_seconds)
    
    async def device_control_loop(self) -> None:
        """Check device states every minute"""
        while True:
            await self.manage_devices()
            await asyncio.sleep(60)

async def main() -> None:
    # Configure logging
    log_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler with rotation (max 5MB per file, keep 5 backup files)
    file_handler = RotatingFileHandler(
        'smarthome.log',
        maxBytes=5*1024*1024,  # 5MB
        backupCount=5
    )
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    logger.info("Starting Smart Home Device Scheduler")
    
    scheduler: DeviceScheduler = DeviceScheduler()
    
    # Store reference to scheduler in web_app
    web_app.scheduler = scheduler
    
    # Get the event loop
    scheduler.loop = asyncio.get_event_loop()
    
    # Start web server
    web_app.start_web_server()
    
    logger.info("Device scheduler started")
    
    # Initial price fetch
    await scheduler.update_prices()
    
    # Run both loops concurrently
    await asyncio.gather(
        scheduler.price_update_loop(),
        scheduler.device_control_loop()
    )

if __name__ == "__main__":
    asyncio.run(main())