#!/usr/bin/env python3
"""
Weather CLI tool that fetches current weather based on IP geolocation.

This script automatically detects your location using IP geolocation services
and retrieves current weather conditions from multiple weather APIs with
intelligent failover and caching.
"""

import json
import logging
import os
import sys
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.request import urlopen, Request
from urllib.parse import quote
from urllib.error import URLError, HTTPError

# Configuration constants
CACHE_FILE = Path.home() / ".weather_cache.json"
CACHE_DURATION = 1800  # 30 minutes in seconds
TIMEOUT = 3.0  # Network request timeout in seconds
LOCATION_TIMEOUT = 2.0  # Timeout for location API requests
WEATHER_TIMEOUT = 2.0  # Timeout for weather API requests
LOCATION_SOURCES_MAX_WORKERS = 2
GEOCODING_RESULTS_LIMIT = 5

# WMO Weather interpretation codes
# Reference: https://open-meteo.com/en/docs
WMO_CONDITIONS = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Foggy",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    95: "Thunderstorm",
}

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if os.environ.get('WEATHER_DEBUG') else logging.WARNING,
    format='%(levelname)s: %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


def get_no_proxy_env() -> Dict[str, str]:
    """
    Create environment dict with proxy settings disabled.

    Returns:
        Environment dictionary with all proxy variables removed.
    """
    env = os.environ.copy()
    env['no_proxy'] = '*'
    proxy_vars = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']
    for var in proxy_vars:
        env.pop(var, None)
    return env


def fetch_url(url: str, timeout: float = TIMEOUT) -> Optional[Dict[str, Any]]:
    """
    Fetch URL and parse JSON response with timeout and error handling.

    Attempts to fetch using urllib first, then falls back to curl if that fails.
    This is useful for environments with proxy issues or urllib limitations.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response as dict, or None if request fails.
    """
    env = get_no_proxy_env()

    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except (URLError, HTTPError, TimeoutError, ConnectionError) as e:
        logger.debug(f"urllib failed for {url}: {e}")

        # Fallback to curl without proxy
        try:
            result = subprocess.run(
                ['curl', '-s', '-m', str(int(timeout)), '-A', 'Mozilla/5.0', '--noproxy', '*', url],
                capture_output=True,
                text=True,
                timeout=timeout + 0.5,
                env=env,
                check=False
            )
            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as curl_error:
            logger.debug(f"curl fallback failed for {url}: {curl_error}")
        except Exception as e:
            logger.debug(f"Unexpected error during curl fallback: {e}")

        logger.warning(f"Failed to fetch {url}: {e}")
        return None


def get_location_parallel() -> Optional[str]:
    """
    Detect current city using IP geolocation from multiple sources.

    Queries multiple geolocation APIs in parallel and returns the first
    successful result to minimize latency.

    Returns:
        City name string, or None if all sources fail.
    """
    sources = [
        "https://ipapi.co/json/",
        "http://ip-api.com/json/"
    ]

    with ThreadPoolExecutor(max_workers=LOCATION_SOURCES_MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_url, url, LOCATION_TIMEOUT): url for url in sources}

        try:
            for future in as_completed(futures, timeout=LOCATION_TIMEOUT + 0.5):
                try:
                    result = future.result()
                    if result:
                        # Parse different API formats
                        city = result.get('city') or result.get('city_name')
                        if city:
                            logger.debug(f"Location detected: {city}")
                            return city
                except Exception as e:
                    logger.debug(f"Error processing location result: {e}")
        except TimeoutError:
            logger.warning("Location detection timed out")

    logger.warning("Failed to detect location from all sources")
    return None


def get_weather_parallel(city: str) -> str:
    """
    Fetch current weather for a city from multiple weather APIs.

    Tries Open-Meteo first (more current data), then falls back to wttr.in.
    Uses sequential requests with failover rather than parallel to prioritize
    the more accurate data source.

    Args:
        city: City name to get weather for.

    Returns:
        Weather string in format "XXF, Description", or "unavailable" if all sources fail.
    """

    def fetch_wttr() -> Optional[str]:
        """Fetch weather from wttr.in API."""
        env = get_no_proxy_env()

        try:
            result = subprocess.run(
                ['curl', '-s', '-m', str(int(WEATHER_TIMEOUT)), '-A', 'curl', '--noproxy', '*',
                 f"https://wttr.in/{quote(city)}?format=j1"],
                capture_output=True,
                text=True,
                timeout=WEATHER_TIMEOUT + 0.5,
                env=env,
                check=False
            )
            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                # Use current_condition which is actual observation data
                current = data['current_condition'][0]
                temp_f = current['temp_F']
                desc = current['weatherDesc'][0]['value']

                logger.debug(f"wttr.in: {temp_f}F, {desc}")
                return f"{temp_f}F, {desc}"
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, IndexError) as e:
            logger.debug(f"wttr.in error: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error from wttr.in: {e}")
        return None

    def fetch_openmeteo() -> Optional[str]:
        """Fetch weather from Open-Meteo API."""
        try:
            # First get coordinates for the city - fetch multiple results to find best match
            geo_url = (
                f"https://geocoding-api.open-meteo.com/v1/search?"
                f"name={quote(city)}&count={GEOCODING_RESULTS_LIMIT}&language=en&format=json"
            )
            geo_data = fetch_url(geo_url, WEATHER_TIMEOUT)
            if not geo_data or not geo_data.get('results'):
                logger.debug("Open-Meteo: No geocoding results found")
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

            logger.debug(f"Open-Meteo location: {location_name}, {admin1}, {country} ({lat}, {lon})")

            weather_url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}&current_weather=true&temperature_unit=fahrenheit"
            )
            weather_data = fetch_url(weather_url, WEATHER_TIMEOUT)
            if not weather_data or 'current_weather' not in weather_data:
                logger.debug("Open-Meteo: No weather data returned")
                return None

            temp = weather_data['current_weather']['temperature']
            wmo_code = weather_data['current_weather']['weathercode']
            desc = WMO_CONDITIONS.get(wmo_code, "Unknown")

            logger.debug(f"Open-Meteo: {temp}F, {desc} (WMO code: {wmo_code})")
            return f"{temp}F, {desc}"

        except (KeyError, IndexError, TypeError) as e:
            logger.debug(f"Open-Meteo data parsing error: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error from Open-Meteo: {e}")
        return None

    # Try open-meteo first (more current data), then wttr.in as fallback
    result = fetch_openmeteo()
    if result:
        return result

    result = fetch_wttr()
    if result:
        return result

    logger.warning(f"All weather sources failed for city: {city}")
    return "unavailable"


def load_cache() -> Optional[Dict[str, Any]]:
    """
    Load cached weather data if still valid.

    Returns:
        Cache dict with 'timestamp', 'city', and 'weather' keys if valid,
        None if cache doesn't exist, is expired, or is corrupted.
    """
    if not CACHE_FILE.exists():
        return None

    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)

        if time.time() - cache.get('timestamp', 0) < CACHE_DURATION:
            logger.debug(f"Using cached data for {cache.get('city')}")
            return cache
        else:
            logger.debug("Cache expired")
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.debug(f"Cache read error: {e}")

    return None


def save_cache(city: str, weather: str) -> None:
    """
    Save weather data to cache file.

    Args:
        city: City name.
        weather: Weather condition string.
    """
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'timestamp': time.time(),
                'city': city,
                'weather': weather
            }, f)
        logger.debug(f"Cached weather data for {city}")
    except (OSError, TypeError) as e:
        logger.warning(f"Failed to save cache: {e}")


def main() -> None:
    """
    Main entry point for the weather CLI tool.

    Checks for manual city override (via command-line args or WEATHER_CITY env var),
    attempts to use cached data, and fetches fresh weather data if needed.
    """
    # Check for manual city override
    manual_city = os.environ.get('WEATHER_CITY')
    if len(sys.argv) > 1:
        manual_city = ' '.join(sys.argv[1:])

    # Try cache first (only if no manual override)
    cache = load_cache()
    if cache and not manual_city:
        city, weather = cache['city'], cache['weather']
    else:
        # Get location
        city = manual_city or get_location_parallel()

        if not city:
            print("You are in an unknown location. Weather is unavailable.")
            return

        # Get weather
        weather = get_weather_parallel(city)

        # Save to cache (only if not manual override and data is valid)
        if not manual_city and weather != "unavailable":
            save_cache(city, weather)

    print(f"You are in {city}. The current weather is {weather}.")


if __name__ == "__main__":
    main()
