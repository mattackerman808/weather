#!/usr/bin/env python3
import json
import os
import sys
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote
from urllib.error import URLError, HTTPError

CACHE_FILE = Path.home() / ".weather_cache.json"
CACHE_DURATION = 1800  # 30 minutes in seconds
TIMEOUT = 3.0  # Timeout for network requests


def fetch_url(url, timeout=TIMEOUT):
    """Fetch URL with timeout and error handling. Falls back to curl if urllib fails."""
    # Temporarily unset proxy for direct connection
    env = os.environ.copy()
    env['no_proxy'] = '*'
    env.pop('http_proxy', None)
    env.pop('https_proxy', None)
    env.pop('HTTP_PROXY', None)
    env.pop('HTTPS_PROXY', None)

    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except (URLError, HTTPError, TimeoutError) as e:
        # Fallback to curl without proxy
        try:
            result = subprocess.run(
                ['curl', '-s', '-m', str(int(timeout)), '-A', 'Mozilla/5.0', '--noproxy', '*', url],
                capture_output=True,
                text=True,
                timeout=timeout + 0.5,
                env=env
            )
            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout)
        except:
            pass

        if os.environ.get('WEATHER_DEBUG'):
            print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def get_location_parallel():
    """Get location from multiple sources in parallel."""
    sources = [
        "https://ipapi.co/json/",
        "http://ip-api.com/json/"
    ]

    with ThreadPoolExecutor(max_workers=len(sources)) as executor:
        futures = {executor.submit(fetch_url, url, 2.0): url for url in sources}

        for future in as_completed(futures, timeout=2.5):
            result = future.result()
            if result:
                # Parse different API formats
                city = result.get('city') or result.get('city_name')
                if city:
                    return city

    return None


def get_weather_parallel(city):
    """Get weather from multiple sources in parallel."""

    def fetch_wttr():
        # Bypass proxy for direct connection
        env = os.environ.copy()
        env['no_proxy'] = '*'
        env.pop('http_proxy', None)
        env.pop('https_proxy', None)
        env.pop('HTTP_PROXY', None)
        env.pop('HTTPS_PROXY', None)

        try:
            result = subprocess.run(
                ['curl', '-s', '-m', '2', '-A', 'curl', '--noproxy', '*', f"https://wttr.in/{quote(city)}?format=j1"],
                capture_output=True,
                text=True,
                timeout=2.5,
                env=env
            )
            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                # Use current_condition which is actual observation data
                current = data['current_condition'][0]
                temp_f = current['temp_F']
                desc = current['weatherDesc'][0]['value']

                if os.environ.get('WEATHER_DEBUG'):
                    obs_time = current.get('observation_time', 'unknown')
                    print(f"wttr.in observation time: {obs_time}, temp: {temp_f}F, desc: {desc}", file=sys.stderr)

                return f"{temp_f}F, {desc}"
        except Exception as e:
            if os.environ.get('WEATHER_DEBUG'):
                print(f"wttr.in error: {e}", file=sys.stderr)
        return None

    def fetch_openmeteo():
        try:
            # First get coordinates for the city - fetch multiple results to find best match
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={quote(city)}&count=5&language=en&format=json"
            geo_data = fetch_url(geo_url, 2.0)
            if not geo_data or not geo_data.get('results'):
                return None

            # Prefer US locations over international ones
            location = None
            for result in geo_data['results']:
                if result.get('country_code') == 'US' or result.get('country') == 'United States':
                    location = result
                    break

            # If no US location found, use first result
            if not location:
                location = geo_data['results'][0]

            lat = location['latitude']
            lon = location['longitude']
            location_name = location.get('name', 'unknown')
            admin1 = location.get('admin1', '')
            country = location.get('country', '')

            if os.environ.get('WEATHER_DEBUG'):
                print(f"open-meteo location: {location_name}, {admin1}, {country} (lat: {lat}, lon: {lon})", file=sys.stderr)

            weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&temperature_unit=fahrenheit"
            weather_data = fetch_url(weather_url, 2.0)
            if not weather_data:
                return None

            temp = weather_data['current_weather']['temperature']
            wmo_code = weather_data['current_weather']['weathercode']

            # Simple WMO code interpretation
            conditions = {
                0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Foggy", 48: "Foggy", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
                61: "Light rain", 63: "Rain", 65: "Heavy rain", 71: "Light snow",
                73: "Snow", 75: "Heavy snow", 95: "Thunderstorm"
            }
            desc = conditions.get(wmo_code, "Unknown")

            if os.environ.get('WEATHER_DEBUG'):
                print(f"open-meteo temp: {temp}F, desc: {desc}, code: {wmo_code}", file=sys.stderr)

            return f"{temp}F, {desc}"
        except Exception as e:
            if os.environ.get('WEATHER_DEBUG'):
                print(f"open-meteo error: {e}", file=sys.stderr)
            return None

    # Try open-meteo first (more current data), then wttr.in as fallback
    result = fetch_openmeteo()
    if result:
        return result

    result = fetch_wttr()
    if result:
        return result

    return "unavailable"


def load_cache():
    """Load cached data if valid."""
    if not CACHE_FILE.exists():
        return None

    try:
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)

        if time.time() - cache.get('timestamp', 0) < CACHE_DURATION:
            return cache
    except:
        pass

    return None


def save_cache(city, weather):
    """Save data to cache."""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump({
                'timestamp': time.time(),
                'city': city,
                'weather': weather
            }, f)
    except:
        pass


def main():
    # Check for manual city override
    manual_city = os.environ.get('WEATHER_CITY')
    if len(sys.argv) > 1:
        manual_city = ' '.join(sys.argv[1:])

    # Try cache first
    cache = load_cache()
    if cache and not manual_city:
        city, weather = cache['city'], cache['weather']
    else:
        # Get location
        city = manual_city or get_location_parallel()

        if not city:
            print("Current city: unknown, weather is unavailable")
            return

        # Get weather
        weather = get_weather_parallel(city)

        # Save to cache (only if not manual override and data is valid)
        if not manual_city and weather != "unavailable":
            save_cache(city, weather)

    print(f"You are in {city}. The current weather is {weather}.")


if __name__ == "__main__":
    main()
