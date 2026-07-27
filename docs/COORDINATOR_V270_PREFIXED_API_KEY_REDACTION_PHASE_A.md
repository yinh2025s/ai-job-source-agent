# v270 Prefixed API-Key Redaction - Phase A

## Trigger

Final `.269` artifact review found a Google Maps key under the field name
`mapsApiKey` in both a stored homepage snapshot and scoped replay tape.

## Root Cause

Sensitive-key normalization recognized exact `apiKey`, `api_key` and `api-key`
names, but not ordinary prefixed names whose canonical form ends in
`apikey`. HTML/JavaScript text sanitation also used a fixed sensitive-field
list.

## Design

- Treat canonical field names ending in `apikey` as sensitive.
- Redact quoted, unquoted, input and meta text fields matching a bounded
  `*apiKey`, `*api_key` or `*api-key` identifier.
- Do not match suffixes after `apiKey`; `apiKeyEnabled` remains semantic.
- Keep the HMG ticket and request-identity rules unchanged.

This is a generic credential contract, not a company or domain exception.

## Acceptance

1. `mapsApiKey`, `google_maps_api_key` and `rapidApiKey` values are absent from
   snapshot bodies and tapes.
2. `apiKeyEnabled` remains unchanged.
3. Related tests and provider/resolver/architecture gates pass.
4. A new isolated `.270` live/replay reproduces all three Haley outcomes.
5. Full artifact credential audit, HMG ticket audit and Exact identity audit
   pass.

Plugin work, authenticated External Apply, coordinator-v2, LLM and sealed
holdouts remain frozen.
