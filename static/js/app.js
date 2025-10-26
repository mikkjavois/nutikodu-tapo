let devices = [];
let deviceStates = {};
let currentStatusData = null;

// Show message
function showMessage(text, isError = false) {
    const msg = document.getElementById('message');
    msg.textContent = text;
    msg.className = 'message ' + (isError ? 'error' : 'success');
    msg.style.display = 'block';
    setTimeout(() => {
        msg.style.display = 'none';
    }, 3000);
}

// Load devices
async function loadDevices() {
    try {
        const response = await fetch('/api/devices');
        const data = await response.json();
        devices = data.devices;
        renderDevices();
    } catch (error) {
        console.error('Error loading devices:', error);
    }
}

// Render devices
function renderDevices() {
    const list = document.getElementById('deviceList');
    if (devices.length === 0) {
        list.innerHTML = '<p style="color: #666;">Seadmeid pole veel lisatud. Lisa seade allpool.</p>';
        return;
    }
    
    list.innerHTML = devices.map(device => {
        const forcedState = device.forced_state;
        const stateValue = forcedState === true ? 'on' : forcedState === false ? 'off' : 'auto';
        
        // Get device state info from status API
        const deviceState = deviceStates[device.name];
        const shouldBeOn = deviceState ? deviceState.should_be_on : false;
        const isReachable = deviceState ? deviceState.is_reachable : true;
        
        // Determine indicator color:
        // - Unreachable: gray
        // - Forced ON: green
        // - Forced OFF: red
        // - Auto + should be on: green
        // - Auto + should be off: blue
        let statusClass, statusText;
        if (!isReachable) {
            statusClass = 'status-unreachable';
            statusText = 'Seade ei ole kättesaadav';
        } else if (forcedState === true) {
            statusClass = 'status-on';
            statusText = 'SEES';
        } else if (forcedState === false) {
            statusClass = 'status-off';
            statusText = 'VÄLJAS';
        } else {
            // Auto mode
            if (shouldBeOn) {
                statusClass = 'status-on';
                statusText = 'Automaatne (SEES)';
            } else {
                statusClass = 'status-auto';
                statusText = 'Automaatne (VÄLJAS)';
            }
        }
        
        return `
            <div class="device-item">
                <h3>${device.name} <span class="status-indicator ${statusClass}" title="${statusText}"></span></h3>
                <div class="ip" style="color: ${isReachable ? '#666' : '#dc3545'};">${device.ip}</div>
                <div class="threshold">
                    <span>Piirhind:</span>
                    <div style="display: flex; gap: 10px; align-items: center;">
                        <input type="number" id="threshold_${device.name}" step="0.1" min="0.1" value="${device.threshold_value}" style="flex: 1;">
                        <select id="type_${device.name}" class="state-select" style="width: 100px;">
                            <option value="multiplier" ${device.threshold_type === 'multiplier' ? 'selected' : ''}>× med</option>
                            <option value="fixed" ${device.threshold_type === 'fixed' ? 'selected' : ''}>s/kWh</option>
                        </select>
                    </div>
                    <button onclick="updateDeviceThreshold('${device.name}')">Uuenda</button>
                </div>
                <div class="device-controls">
                    <select class="state-select" onchange="forceState('${device.name}', this.value)">
                        <option value="auto" ${stateValue === 'auto' ? 'selected' : ''}>Automaatne</option>
                        <option value="on" ${stateValue === 'on' ? 'selected' : ''}>SEES</option>
                        <option value="off" ${stateValue === 'off' ? 'selected' : ''}>VÄLJAS</option>
                    </select>
                    <button class="btn btn-delete" onclick="deleteDevice('${device.name}')">Kustuta</button>
                </div>
            </div>
        `;
    }).join('');
    
    // Update chart device selector
    updateChartDeviceSelector();
}

// Update chart device selector with current devices
function updateChartDeviceSelector() {
    const selector = document.getElementById('chartDeviceSelector');
    const currentSelection = selector.value;
    
    // Keep "All (median)" option and add devices
    selector.innerHTML = '<option value="">-</option>' + 
        devices.map(device => 
            `<option value="${device.name}">${device.name}</option>`
        ).join('');
    
    // Restore previous selection if still valid
    if (currentSelection && devices.some(d => d.name === currentSelection)) {
        selector.value = currentSelection;
    }
}

// Add device
async function addDevice() {
    const name = document.getElementById('deviceName').value.trim();
    const ip = document.getElementById('deviceIP').value.trim();
    const thresholdValue = document.getElementById('deviceThreshold').value;
    const thresholdType = document.getElementById('thresholdTypeSelect').value;
    
    if (!name || !ip) {
        showMessage('Palun sisesta nii nimi kui ka IP aadress', true);
        return;
    }
    
    try {
        const response = await fetch('/api/devices', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                name, 
                ip, 
                threshold_type: thresholdType,
                threshold_value: thresholdValue
            })
        });
        
        const data = await response.json();
        if (response.ok) {
            showMessage(data.message);
            document.getElementById('deviceName').value = '';
            document.getElementById('deviceIP').value = '';
            document.getElementById('deviceThreshold').value = '1.5';
            
            // Wait a bit for backend to calculate timeframes
            await new Promise(resolve => setTimeout(resolve, 500));
            
            // Reload devices and status to get updated data
            await loadDevices();
            await loadStatus();
        } else {
            showMessage(data.error, true);
        }
    } catch (error) {
        showMessage('Viga seadme lisamisel: ' + error.message, true);
    }
}

// Update device threshold
async function updateDeviceThreshold(name) {
    const thresholdValue = document.getElementById(`threshold_${name}`).value;
    const thresholdType = document.getElementById(`type_${name}`).value;
    
    try {
        const response = await fetch(`/api/devices/${encodeURIComponent(name)}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                threshold_type: thresholdType,
                threshold_value: thresholdValue
            })
        });
        
        const data = await response.json();
        if (response.ok) {
            showMessage(`Seadme ${name} piirhind uuendatud`);
            
            // Wait a bit for backend to recalculate timeframes
            await new Promise(resolve => setTimeout(resolve, 500));
            
            // Reload devices from API to get updated threshold
            await loadDevices();
            
            // Then reload status to get new timeframes and device states
            await loadStatus();
        } else {
            showMessage(data.error, true);
        }
    } catch (error) {
        showMessage('Viga läve uuendamisel: ' + error.message, true);
    }
}

// Delete device
async function deleteDevice(name) {
    if (!confirm(`Kas oled kindel, et soovid kustutada seadme ${name}?`)) return;
    
    try {
        const response = await fetch(`/api/devices/${encodeURIComponent(name)}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        if (response.ok) {
            showMessage(data.message);
            loadDevices();
        } else {
            showMessage(data.error, true);
        }
    } catch (error) {
        showMessage('Viga seadme kustutamisel: ' + error.message, true);
    }
}

// Force device state
async function forceState(name, state) {
    try {
        const response = await fetch(`/api/devices/${encodeURIComponent(name)}/force`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({state})
        });
        
        const data = await response.json();
        if (response.ok) {
            showMessage(data.message);
            loadDevices();
            loadStatus();
        } else {
            showMessage(data.error, true);
        }
    } catch (error) {
        showMessage('Viga oleku sundmisel: ' + error.message, true);
    }
}

// Load status - gets device states and timeframes
async function loadStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        currentStatusData = data;
        
        // Store device states for use in rendering
        deviceStates = data.device_states || {};
        
        // Re-render devices to update indicators
        renderDevices();
        
        // Re-render chart if a device is selected
        const chartSelector = document.getElementById('chartDeviceSelector');
        if (chartSelector && chartSelector.value) {
            renderPriceChart();
        }
    } catch (error) {
        console.error('Viga oleku laadimisel:', error);
    }
}

// Initial load
loadDevices();
loadStatus();

// Refresh device status every 1 minute
setInterval(() => {
    loadDevices();
    // Update device states for indicator colors
    fetch('/api/status')
        .then(response => response.json())
        .then(data => {
            deviceStates = data.device_states || {};
            renderDevices();
        })
        .catch(error => console.error('Viga oleku laadimisel:', error));
}, 60000); // Every 60 seconds (1 minute)

// Price Chart Functions
let priceChartData = null;
let chartPadding = null;
let chartDimensions = null;

async function loadPriceData() {
    try {
        const response = await fetch('/api/prices');
        const data = await response.json();
        priceChartData = data;
        renderPriceChart();
    } catch (error) {
        console.error('Error loading price data:', error);
    }
}

function renderPriceChart() {
    if (!priceChartData || !priceChartData.prices || priceChartData.prices.length === 0) {
        return;
    }
    
    const canvas = document.getElementById('chartCanvas');
    const ctx = canvas.getContext('2d');
    
    // Set canvas size
    const container = document.getElementById('priceChart');
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;
    
    const prices = priceChartData.prices;
    const median = priceChartData.median;
    const currentTime = priceChartData.current_time;
    
    // Get selected device
    const chartSelector = document.getElementById('chartDeviceSelector');
    const selectedDevice = chartSelector ? chartSelector.value : '';
    
    // Get device-specific threshold or use median
    let thresholdPrice = median;
    let thresholdLabel = `Mediaan: ${median.toFixed(2)} s/kWh`;
    let deviceTimeframes = [];
    
    if (selectedDevice && currentStatusData && currentStatusData.timeframes) {
        const device = devices.find(d => d.name === selectedDevice);
        if (device) {
            // Calculate threshold based on type
            if (device.threshold_type === 'fixed') {
                thresholdPrice = device.threshold_value;
                thresholdLabel = `Piirhind: ${thresholdPrice.toFixed(2)} s/kWh (fikseeritud)`;
            } else {
                thresholdPrice = median * device.threshold_value;
                thresholdLabel = `Piirhind: ${thresholdPrice.toFixed(2)} s/kWh (${device.threshold_value}× mediaan)`;
            }
            deviceTimeframes = currentStatusData.timeframes[selectedDevice] || [];
        }
    }
    
    // Calculate chart dimensions
    const padding = { top: 20, right: 40, bottom: 60, left: 50 };
    const chartWidth = canvas.width - padding.left - padding.right;
    const chartHeight = canvas.height - padding.top - padding.bottom;
    
    // Store for hover detection
    chartPadding = padding;
    chartDimensions = { chartWidth, chartHeight, canvas };
    
    // Find min and max prices for scaling
    const priceValues = prices.map(p => p.price);
    const minPrice = Math.min(...priceValues);
    const maxPrice = Math.max(...priceValues);
    const priceRange = maxPrice - minPrice;
    
    // Add 10% padding to the price range, but ensure min is never negative
    const paddedMin = Math.max(0, minPrice - priceRange * 0.1);
    const paddedMax = maxPrice + priceRange * 0.1;
    const paddedRange = paddedMax - paddedMin;
    
    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    // Draw background
    ctx.fillStyle = '#f9f9f9';
    ctx.fillRect(padding.left, padding.top, chartWidth, chartHeight);
    
    // Calculate bar width
    const barWidth = chartWidth / prices.length;
    
    // Helper function to check if a timestamp is within device timeframes
    function isDeviceOn(timestamp) {
        if (!selectedDevice || deviceTimeframes.length === 0) {
            return null; // No device selected or no timeframes
        }
        
        for (const tf of deviceTimeframes) {
            const startTime = new Date(tf.start).getTime() / 1000;
            const endTime = new Date(tf.end).getTime() / 1000;
            if (timestamp >= startTime && timestamp < endTime) {
                return true;
            }
        }
        return false;
    }
    
    // Draw bars
    prices.forEach((priceData, index) => {
        const barHeight = ((priceData.price - paddedMin) / paddedRange) * chartHeight;
        const x = padding.left + index * barWidth;
        const y = padding.top + chartHeight - barHeight;
        
        // Determine bar color
        const isPastTime = priceData.timestamp < currentTime;
        let barColor;
        
        if (isPastTime) {
            barColor = '#cccccc'; // Gray for past
        } else if (selectedDevice) {
            // Device selected: show green if ON, red if OFF
            const deviceOn = isDeviceOn(priceData.timestamp);
            barColor = deviceOn ? '#4ade80' : '#f87171'; // Green for ON, Red for OFF
        } else {
            // No device selected: show orange
            barColor = '#fb923c';
        }
        
        ctx.fillStyle = barColor;
        ctx.fillRect(x + 1, y, barWidth - 2, barHeight);
        
        // Draw time label every 4 hours (every 16th bar, since each bar is 15 min)
        if (index % 16 === 0) {
            const date = new Date(priceData.timestamp * 1000);
            const timeLabel = date.getHours().toString().padStart(2, '0') + ':00';
            
            ctx.fillStyle = '#666';
            ctx.font = '11px Segoe UI';
            ctx.textAlign = 'center';
            ctx.fillText(timeLabel, x + barWidth / 2, canvas.height - padding.bottom + 20);
            
            // Draw date label if it's midnight
            if (date.getHours() === 0) {
                const dateLabel = date.getDate() + '.' + (date.getMonth() + 1) + '.';
                ctx.fillText(dateLabel, x + barWidth / 2, canvas.height - padding.bottom + 35);
            }
        }
    });
    
    // Draw threshold line (median or device-specific threshold)
    const thresholdY = padding.top + chartHeight - ((thresholdPrice - paddedMin) / paddedRange) * chartHeight;
    ctx.strokeStyle = '#667eea';
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(padding.left, thresholdY);
    ctx.lineTo(padding.left + chartWidth, thresholdY);
    ctx.stroke();
    ctx.setLineDash([]);
    
    // Draw threshold label
    ctx.fillStyle = '#667eea';
    ctx.font = 'bold 12px Segoe UI';
    ctx.textAlign = 'right';
    ctx.fillText(thresholdLabel, canvas.width - padding.right + 35, thresholdY - 5);
    
    // Draw Y-axis labels
    ctx.fillStyle = '#666';
    ctx.font = '11px Segoe UI';
    ctx.textAlign = 'right';
    
    const numYLabels = 5;
    for (let i = 0; i <= numYLabels; i++) {
        const price = paddedMin + (paddedRange * i / numYLabels);
        const y = padding.top + chartHeight - (chartHeight * i / numYLabels);
        ctx.fillText(price.toFixed(1), padding.left - 10, y + 4);
        
        // Draw grid line
        ctx.strokeStyle = '#e0e0e0';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(padding.left + chartWidth, y);
        ctx.stroke();
    }
    
    // Draw Y-axis label
    ctx.save();
    ctx.translate(15, canvas.height / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillStyle = '#666';
    ctx.font = 'bold 12px Segoe UI';
    ctx.textAlign = 'center';
    ctx.fillText('Hind (senti/kWh)', 0, 0);
    ctx.restore();
    
    // Draw current time indicator
    const currentIndex = prices.findIndex(p => p.timestamp >= currentTime);
    if (currentIndex !== -1 && currentIndex > 0) {
        const x = padding.left + currentIndex * barWidth;
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(x, padding.top);
        ctx.lineTo(x, padding.top + chartHeight);
        ctx.stroke();
        
        // Draw "Praegu" label
        ctx.fillStyle = '#ef4444';
        ctx.font = 'bold 11px Segoe UI';
        ctx.textAlign = 'center';
        ctx.fillText('Praegu', x, padding.top - 5);
    }
}

// Load price chart on page load and update every 5 minutes
loadPriceData();
setInterval(loadPriceData, 300000); // Every 5 minutes

// Redraw chart on window resize
window.addEventListener('resize', () => {
    if (priceChartData) {
        renderPriceChart();
    }
});

// Add hover functionality to show price tooltip
const canvas = document.getElementById('chartCanvas');
const tooltip = document.createElement('div');
tooltip.style.position = 'absolute';
tooltip.style.backgroundColor = 'rgba(0, 0, 0, 0.8)';
tooltip.style.color = 'white';
tooltip.style.padding = '8px 12px';
tooltip.style.borderRadius = '6px';
tooltip.style.fontSize = '13px';
tooltip.style.fontWeight = '600';
tooltip.style.pointerEvents = 'none';
tooltip.style.display = 'none';
tooltip.style.zIndex = '1000';
tooltip.style.whiteSpace = 'nowrap';
document.getElementById('priceChart').appendChild(tooltip);

canvas.addEventListener('mousemove', (e) => {
    if (!priceChartData || !priceChartData.prices || !chartPadding || !chartDimensions) {
        return;
    }
    
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    
    const prices = priceChartData.prices;
    const barWidth = chartDimensions.chartWidth / prices.length;
    
    // Check if mouse is within chart area
    if (x >= chartPadding.left && x <= chartPadding.left + chartDimensions.chartWidth &&
        y >= chartPadding.top && y <= chartPadding.top + chartDimensions.chartHeight) {
        
        // Find which bar is being hovered
        const barIndex = Math.floor((x - chartPadding.left) / barWidth);
        
        if (barIndex >= 0 && barIndex < prices.length) {
            const priceData = prices[barIndex];
            const date = new Date(priceData.timestamp * 1000);
            const timeStr = date.getHours().toString().padStart(2, '0') + ':' + 
                           date.getMinutes().toString().padStart(2, '0');
            const dateStr = date.getDate() + '.' + (date.getMonth() + 1) + '.';
            
            tooltip.innerHTML = `${dateStr} ${timeStr}<br>${priceData.price.toFixed(2)} senti/kWh`;
            tooltip.style.display = 'block';
            tooltip.style.left = (e.clientX - rect.left + 15) + 'px';
            tooltip.style.top = (e.clientY - rect.top - 40) + 'px';
            
            canvas.style.cursor = 'pointer';
        } else {
            tooltip.style.display = 'none';
            canvas.style.cursor = 'default';
        }
    } else {
        tooltip.style.display = 'none';
        canvas.style.cursor = 'default';
    }
});

canvas.addEventListener('mouseleave', () => {
    tooltip.style.display = 'none';
    canvas.style.cursor = 'default';
});
