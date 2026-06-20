"""
Zero-dependency parser for the UNENCRYPTED metadata header of a .wowsreplay file.

A .wowsreplay file is laid out as:
    magic        : 4 bytes
    blocks_count : uint32 (little-endian)
    block_1_len  : uint32
    block_1_data : block_1_len bytes  <- JSON metadata (UTF-8), unencrypted
    [block_2_len : uint32]
    [block_2_data: ...]                <- optional second JSON block (arena/results)
    <rest>       : Blowfish-encrypted + zlib-compressed packet stream

This module only reads the unencrypted JSON block(s). That alone gives us:
version, map, players, ships, game mode, date — enough to gate ingestion on
client version and to label a clip. Decoding the packet stream (events) needs
replays_unpack and is handled separately.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


def parse_metadata(path: str | Path) -> dict:
    """Return the parsed JSON metadata blocks from a .wowsreplay file."""
    data = Path(path).read_bytes()
    offset = 0

    magic = data[offset:offset + 4]
    offset += 4
    blocks_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    blocks: list[dict] = []
    # The first block is always the metadata JSON. Some replays carry a second
    # JSON block. We read up to blocks_count JSON blocks defensively.
    for _ in range(max(blocks_count, 1)):
        if offset + 4 > len(data):
            break
        block_len = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        # Sanity guard: a JSON block should be well under the file size.
        if block_len == 0 or offset + block_len > len(data):
            break
        raw = data[offset:offset + block_len]
        offset += block_len
        try:
            blocks.append(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Hit the encrypted packet stream; stop.
            break

    return {
        "magic": magic.hex(),
        "blocks_count": blocks_count,
        "metadata": blocks[0] if blocks else {},
        "extra_blocks": blocks[1:],
        "packet_stream_offset": offset,
        "file_size": len(data),
    }


def summarize(parsed: dict) -> dict:
    """Pull the fields that matter for ingestion + labeling into a flat dict."""
    m = parsed.get("metadata", {})
    vehicles = m.get("vehicles", [])
    player_name = m.get("playerName")
    player_vehicle = None
    for v in vehicles:
        if v.get("name") == player_name:
            player_vehicle = v.get("shipId") or v.get("name")
            break
    return {
        "client_version": m.get("clientVersionFromExe") or m.get("clientVersionFromXml"),
        "date_time": m.get("dateTime"),
        "map_display_name": m.get("mapDisplayName") or m.get("mapName"),
        "game_mode": m.get("scenario") or m.get("gameMode"),
        "matchGroup": m.get("matchGroup"),
        "player_name": player_name,
        "player_ship": m.get("playerVehicle"),
        "players_count": len(vehicles),
        "duration_s": m.get("duration"),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python parse_header.py <path-to.wowsreplay>")
        raise SystemExit(2)

    parsed = parse_metadata(sys.argv[1])
    print("=== raw header info ===")
    print(f"magic={parsed['magic']}  blocks_count={parsed['blocks_count']}  "
          f"packet_stream_offset={parsed['packet_stream_offset']}  "
          f"file_size={parsed['file_size']}")
    print("\n=== summary ===")
    print(json.dumps(summarize(parsed), indent=2, ensure_ascii=False))
    print("\n=== full metadata keys ===")
    print(sorted(parsed["metadata"].keys()))
