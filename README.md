# Weather CLI Tool

A fast, production-ready Python CLI tool that automatically detects your location and displays current weather conditions. Features intelligent API failover, caching, and minimal dependencies.

## Features

- **Automatic Location Detection**: Uses IP geolocation to determine your current city
- **Multiple Weather Sources**: Fetches from Open-Meteo (primary) and wttr.in (fallback) for reliability
- **Smart Caching**: Caches results for 30 minutes to minimize API calls and improve speed
- **Parallel Requests**: Queries multiple geolocation sources simultaneously for faster results
- **Proxy Handling**: Automatically bypasses proxies that may interfere with API requests
- **Resilient Error Handling**: Gracefully handles network failures and API errors
- **Manual City Override**: Specify any city via command-line or environment variable
- **Debug Mode**: Optional verbose logging for troubleshooting
- **Type-Safe**: Fully type-hinted for better code quality and IDE support

## Installation

### Requirements

- Python 3.7+
- `curl` command-line tool (for proxy bypass fallback)
- Internet connection

### Setup

1. Clone or download the repository:
```bash
git clone <repository-url>
cd weather
```

2. Make the script executable:
```bash
chmod +x weather.py
```

3. (Optional) Add to your PATH or create an alias:
```bash
# Add to ~/.bashrc or ~/.zshrc
alias weather='/path/to/weather/weather.py'
```

## Usage

### Basic Usage

Run the script without arguments to get weather for your current location:

```bash
./weather.py
```

Output:
```
You are in San Francisco. The current weather is 62F, Partly cloudy.
```

### Specify a City

Pass a city name as an argument:

```bash
./weather.py New York
./weather.py "Los Angeles"
./weather.py Tokyo
```

### Using Environment Variable

Set the `WEATHER_CITY` environment variable:

```bash
export WEATHER_CITY="Seattle"
./weather.py
```

### Debug Mode

Enable detailed logging to troubleshoot issues:

```bash
WEATHER_DEBUG=1 ./weather.py
```

This will show:
- Which APIs are being queried
- Response times and failures
- Cache hits/misses
- Geocoding results

## How It Works

### Architecture Overview

The tool follows a multi-stage approach with intelligent failover:

```
┌─────────────────┐
│  User Request   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Check Cache    │ ← Cache valid for 30 minutes
└────────┬────────┘
         │ (cache miss or manual city)
         ▼
┌─────────────────┐
│ Get Location    │ ← Parallel requests to:
│  (if needed)    │   - ipapi.co
└────────┬────────┘   - ip-api.com
         │
         ▼
┌─────────────────┐
│  Get Weather    │ ← Sequential requests:
│                 │   1. Open-Meteo (preferred)
└────────┬────────┘   2. wttr.in (fallback)
         │
         ▼
┌─────────────────┐
│  Save Cache     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Display Result  │
└─────────────────┘
```

### Component Details

#### 1. Location Detection (`get_location_parallel`)

- Queries multiple IP geolocation APIs in parallel using `ThreadPoolExecutor`
- Returns the first successful result to minimize latency
- APIs used:
  - **ipapi.co**: Free IP geolocation service
  - **ip-api.com**: Free IP geolocation with high rate limits
- Timeout: 2 seconds per request

#### 2. Weather Fetching (`get_weather_parallel`)

Uses a sequential failover approach (not parallel) to prioritize data quality:

**Primary: Open-Meteo**
- Free weather API with no API key required
- Two-step process:
  1. Geocode city name to coordinates (prefers US locations)
  2. Fetch current weather data using coordinates
- Returns temperature in Fahrenheit with WMO weather codes
- More current and accurate data

**Fallback: wttr.in**
- Simple weather API accessed via curl
- Returns current observation data
- Used only if Open-Meteo fails

#### 3. Network Resilience

**Proxy Bypass**
- Automatically disables HTTP/HTTPS proxy settings
- Useful in corporate environments where proxies may block API access
- Sets `no_proxy=*` and removes proxy environment variables

**Dual HTTP Client**
- Primary: Python's `urllib` for standard requests
- Fallback: `curl` subprocess for when urllib fails
- Handles timeouts, connection errors, and malformed responses

#### 4. Caching (`load_cache`, `save_cache`)

- Cache file: `~/.weather_cache.json`
- Cache duration: 30 minutes (1800 seconds)
- Cached data includes:
  - Timestamp
  - City name
  - Weather string
- Cache invalidated when:
  - More than 30 minutes old
  - Manual city override provided
  - Cache file corrupted or missing

#### 5. Error Handling

- Specific exception catching (no bare `except` clauses)
- Graceful degradation when APIs fail
- Informative error messages in debug mode
- Non-zero exit handling for subprocess calls

## Configuration

### Constants (in `weather.py`)

You can modify these constants to tune behavior:

```python
CACHE_FILE = Path.home() / ".weather_cache.json"  # Cache file location
CACHE_DURATION = 1800  # Cache validity in seconds (30 min)
TIMEOUT = 3.0  # Default network timeout
LOCATION_TIMEOUT = 2.0  # Geolocation API timeout
WEATHER_TIMEOUT = 2.0  # Weather API timeout
GEOCODING_RESULTS_LIMIT = 5  # Number of geocoding results to fetch
```

### WMO Weather Codes

The script interprets WMO (World Meteorological Organization) weather codes from Open-Meteo:

| Code | Condition |
|------|-----------|
| 0 | Clear |
| 1 | Mainly clear |
| 2 | Partly cloudy |
| 3 | Overcast |
| 45, 48 | Foggy |
| 51, 53, 55 | Drizzle (light to heavy) |
| 61, 63, 65 | Rain (light to heavy) |
| 71, 73, 75 | Snow (light to heavy) |
| 95 | Thunderstorm |

## API Sources

### Geolocation APIs

1. **ipapi.co** (https://ipapi.co/)
   - Free tier: 1,000 requests/day
   - No API key required
   - Returns city, region, country, coordinates

2. **ip-api.com** (http://ip-api.com/)
   - Free tier: 45 requests/minute
   - No API key required
   - Returns city and location data

### Weather APIs

1. **Open-Meteo** (https://open-meteo.com/)
   - Completely free, no API key
   - Open-source weather API
   - High-quality data from national weather services
   - Geocoding + weather forecast endpoints

2. **wttr.in** (https://wttr.in/)
   - Free console-oriented weather service
   - No API key required
   - Returns current observations

## Troubleshooting

### Location Not Detected

If location detection fails:
- Check internet connectivity
- Try specifying city manually: `./weather.py "Your City"`
- Enable debug mode: `WEATHER_DEBUG=1 ./weather.py`
- Verify geolocation APIs are accessible (not blocked by firewall)

### Weather Unavailable

If weather fetch fails:
- Verify the city name is correct
- Check if weather APIs are accessible
- Try a different city to test connectivity
- Enable debug mode to see detailed error messages

### Proxy Issues

If you're behind a corporate proxy:
- The tool automatically bypasses proxies
- Ensure `curl` is installed and accessible
- Check if direct internet access is allowed

### Cache Issues

To clear the cache:
```bash
rm ~/.weather_cache.json
```

## Development

### Type Checking

The codebase is fully type-hinted. Run type checking with:
```bash
mypy weather.py
```

### Code Quality

The code follows Python best practices:
- Type hints for all functions
- Comprehensive docstrings
- Specific exception handling
- No bare `except` clauses
- Proper resource management (context managers)
- Logging instead of print statements for debugging

## License

[Your License Here]

## Contributing

Contributions welcome! Please ensure:
- Type hints are included
- Error handling is specific
- Docstrings follow Google style
- Code passes type checking (mypy)
