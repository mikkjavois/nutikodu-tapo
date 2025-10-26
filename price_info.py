import requests
from datetime import datetime, timedelta, timezone
import statistics
import logging

logger = logging.getLogger(__name__)

def fetch_electricity_prices():
    """
    Fetch electricity prices for Estonia from Elering API
    Returns list of price data for today and tomorrow with prices in senti/kWh
    """
    url = "https://dashboard.elering.ee/api/nps/price"
    
    # Fetch from start of today to end of tomorrow
    start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=2)  # Changed from 1 to 2
    
    params = {
        'start': start_date.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'end': end_date.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    }
    
    logger.debug(f"Fetching electricity prices from {params['start']} to {params['end']}")
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        ee_prices = data.get('data', {}).get('ee', [])
        for price_data in ee_prices:
            price_data['price'] = price_data['price'] / 10
        
        logger.info(f"Successfully fetched {len(ee_prices)} price entries")
        return ee_prices
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching prices: {e}")
        return []

def get_current_price(prices):
    """Get the current electricity price in senti/kWh"""
    if not prices:
        return None
    
    current_time = datetime.now()
    
    for price_data in prices:
        price_time = datetime.fromtimestamp(price_data['timestamp'])
        if price_time.hour == current_time.hour and price_time.date() == current_time.date():
            return price_data['price']
    
    return None

def get_price_statistics(prices):
    """Get price statistics for the day"""
    if not prices:
        return None
    
    price_values = [p['price'] for p in prices]
    return {
        'min': min(price_values),
        'max': max(price_values),
        'mean': statistics.mean(price_values),
        'median': statistics.median(price_values)
    }

def efficient_timeframes(prices, threshold_price, min_duration_minutes=0):
    """
    Get timeframes where the price is below a threshold
    Groups consecutive cheap slots and filters by minimum duration
    
    Args:
        prices: List of price data
        threshold_price: Fixed threshold price in senti/kWh
        min_duration_minutes: Minimum consecutive duration to include a timeframe (default 0)
    
    Returns:
        List of tuples (start_time, end_time, avg_price, duration_minutes)
    """
    if not prices:
        return []
    
    # Determine threshold price
    threshold = threshold_price
    logger.debug(f"Using fixed threshold={threshold:.2f} senti/kWh")
    
    # Mark slots as cheap or expensive
    is_cheap = [p['price'] < threshold for p in prices]
    
    # Group consecutive cheap periods
    all_timeframes = []
    i = 0
    while i < len(prices):
        if is_cheap[i]:
            start_idx = i
            frame_prices = []
            
            # Collect consecutive cheap slots
            while i < len(prices) and is_cheap[i]:
                frame_prices.append(prices[i]['price'])
                i += 1
            
            start_time = datetime.fromtimestamp(prices[start_idx]['timestamp'])
            end_time = datetime.fromtimestamp(prices[i-1]['timestamp']) + timedelta(minutes=15)
            avg_price = statistics.mean(frame_prices)
            duration_minutes = len(frame_prices) * 15
            
            # Only include if duration meets minimum requirement
            if duration_minutes >= min_duration_minutes:
                all_timeframes.append((start_time, end_time, avg_price, duration_minutes))
            else:
                logger.debug(f"Skipping timeframe {start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')} (duration {duration_minutes}min < {min_duration_minutes}min)")
        else:
            i += 1

    logger.debug(f"Found {len(all_timeframes)} timeframes meeting minimum duration of {min_duration_minutes} minutes")
    return all_timeframes

if __name__ == "__main__":
    prices = fetch_electricity_prices()
    
    if prices:
        print("Electricity Prices in Estonia (senti/kWh):")
        print("-" * 50)
        for price_data in prices:
            timestamp = datetime.fromtimestamp(price_data['timestamp'])
            print(f"{timestamp.strftime('%Y-%m-%d %H:%M')}: {price_data['price']:.2f} senti/kWh")
        
        current = get_current_price(prices)
        if current:
            print(f"\nCurrent price: {current:.2f} senti/kWh")
        
        stats = get_price_statistics(prices)
        print(f"\nPrice Statistics:")
        print(f"  Min: {stats['min']:.2f} senti/kWh")
        print(f"  Max: {stats['max']:.2f} senti/kWh")
        print(f"  Mean: {stats['mean']:.2f} senti/kWh")
        print(f"  Median: {stats['median']:.2f} senti/kWh")
        
        threshold_price = stats['median'] * 1.2
        efficient = efficient_timeframes(prices, threshold_price=threshold_price)
        print(f"\nEfficient timeframes (below {threshold_price:.2f} senti/kWh, min 30min):")
        for start, end, avg, duration in efficient:
            print(f"  {start.strftime('%H:%M')} - {end.strftime('%H:%M')} ({duration}min): Avg {avg:.2f} senti/kWh")
