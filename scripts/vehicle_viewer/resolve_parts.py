"""Resolve a BeamNG .pc default-trim config into the set of mesh node names
that should be kept from the vehicle's combined .dae file.

BeamNG's .jbeam files are not strict JSON (comments, trailing commas, and
occasional juxtaposed [] {} with no separating comma), so instead of parsing
the whole file we do targeted balanced-delimiter extraction of just each
part's "flexbodies" block and regex out the mesh names from that substring.

Usage: python resolve_parts.py <vehicle_dir> <pc_file> [out.json]
"""
import json
import re
import sys
from pathlib import Path


def find_balanced(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Given text[start] == open_ch, return the index just after the matching close_ch."""
    depth = 0
    in_string = False
    escape = False
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    raise ValueError("unbalanced delimiters")


def extract_part_block(text: str, part_name: str) -> str | None:
    pattern = re.compile(r'"' + re.escape(part_name) + r'"\s*:\s*\{')
    m = pattern.search(text)
    if not m:
        return None
    brace_start = m.end() - 1
    brace_end = find_balanced(text, brace_start, '{', '}')
    return text[brace_start:brace_end]


def extract_flexbody_meshes(part_block: str) -> set[str]:
    m = re.search(r'"flexbodies"\s*:\s*\[', part_block)
    if not m:
        return set()
    bracket_start = m.end() - 1
    bracket_end = find_balanced(part_block, bracket_start, '[', ']')
    flex_text = part_block[bracket_start:bracket_end]
    # Each entry is a list whose first element (when a string) is a mesh name,
    # except the header row ["mesh", "[group]:", ...].
    names = set()
    for entry_match in re.finditer(r'\[\s*"([^"]+)"', flex_text):
        name = entry_match.group(1)
        if name in ("mesh", "[group]:"):
            continue
        names.add(name)
    return names


def main():
    vehicle_dir = Path(sys.argv[1])
    pc_file = Path(sys.argv[2])

    pc = json.loads(pc_file.read_text(encoding="utf-8"))
    main_part = pc["mainPartName"]
    part_names = {main_part} | {v for v in pc["parts"].values() if v}

    jbeam_files = list(vehicle_dir.glob("*.jbeam"))
    print(f"Scanning {len(jbeam_files)} jbeam files for {len(part_names)} part definitions...")

    jbeam_texts = {jf: jf.read_text(encoding="utf-8", errors="replace") for jf in jbeam_files}

    resolved = {}
    mesh_names = set()
    for part in part_names:
        found_in = None
        for jf, text in jbeam_texts.items():
            block = extract_part_block(text, part)
            if block is not None:
                found_in = jf.name
                meshes = extract_flexbody_meshes(block)
                mesh_names |= meshes
                resolved[part] = {"file": jf.name, "meshes": sorted(meshes)}
                break
        if not found_in:
            resolved[part] = None

    missing = sorted(k for k, v in resolved.items() if v is None)
    print(f"[INFO] {len(missing)} part names had no jbeam definition found "
          f"(likely non-visual: ECU/diff/physics-only): {missing[:15]}")

    out = {
        "main_part": main_part,
        "resolved_parts": {k: v for k, v in resolved.items() if v is not None},
        "missing_parts": missing,
        "mesh_names": sorted(mesh_names),
    }
    out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("resolved_meshes.json")
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Resolved {len(mesh_names)} mesh names -> {out_path}")


if __name__ == "__main__":
    main()
