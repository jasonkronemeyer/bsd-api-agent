# MCP Reference

Server entry point: mcp_server.py
Transport: stdio

## Available tools

## check_new_releases

Input:
- none

Output:
- new_releases: list of {data_type, as_of_date}
- total_new: integer

## list_release_files

Input:
- data_type: availability | challenge
- as_of_date: YYYY-MM-DD

Output:
- data_type
- as_of_date
- total_files
- total_size_bytes
- total_size_gb
- by_category (map)

## download_release

Input (all optional except when you want to scope):
- data_type: availability | challenge
- as_of_date: YYYY-MM-DD
- recheck: boolean (default false)

Output summary:
- releases_processed
- files_downloaded
- files_ingested
- files_errored

Notes:
- This can be long-running for full national downloads.
- Uses the same manifest, downloader, and ingestion logic as CLI.

## get_download_status

Input:
- none

Output:
- dates: list of known releases with status
- file_counts: counts by file status

## Example Claude Desktop MCP config

```json
{
  "mcpServers": {
    "bdc": {
      "command": "python",
      "args": ["/absolute/path/to/bsd-api-agent/mcp_server.py"],
      "env": {
        "FCC_USERNAME": "your@email.com",
        "FCC_API_TOKEN": "your_hash_value"
      }
    }
  }
}
```
