# Local QWeather configuration

Copy the committed, empty `QWEATHER.env.example` to `backend/.env.weather` and fill in the two values on the local machine:

```dotenv
CITERAG_QWEATHER_API_HOST=
CITERAG_QWEATHER_API_KEY=
```

The Host is the dedicated `*.qweatherapi.com` hostname from the QWeather console, without a scheme, port, or path. The real `.env.weather` file is ignored by Git; never commit it or send its contents in chat. The example must remain empty. This is the only local dotenv-style file CiteRAG reads; general `.env` loading remains disabled.

At API startup, a complete local pair enables the three weather tools. A missing file or two blank values leave weather disabled; one missing value or an unexpected entry produces a sanitized configuration error. Process environment Host and Key take priority as a pair and never mix with file values. An explicit `CITERAG_QWEATHER_ENABLED=false` disables the tools. Restart the API after editing the file; the running process does not reload settings.

The file contains a plaintext API Key. Keep it accessible only to the local account and remove it when no longer needed. To invalidate a Key, revoke it in the QWeather console as well.
