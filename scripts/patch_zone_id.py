"""One-time script to add zone_id to existing metadata.json files."""
import json
import pathlib

recordings = pathlib.Path(__file__).parent.parent / "recordings"
patched = 0

for meta_file in recordings.glob("*/metadata.json"):
    data = json.loads(meta_file.read_text(encoding="utf-8"))
    if data.get("zone_id"):
        continue

    # Guess zone from team_ids
    guessed = None
    for t in data.get("teams", []):
        tid = t.get("team_id", "")
        if "_" in tid:
            guessed = tid.rsplit("_", maxsplit=2)[0]  # cs_team1 -> cs
            break

    data["zone_id"] = guessed or "cs"
    meta_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    patched += 1
    print(f"Patched {meta_file.parent.name}: zone_id={data['zone_id']}")

print(f"Done, {patched} files patched.")
