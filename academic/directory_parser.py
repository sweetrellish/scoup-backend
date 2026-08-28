"""Parse SUdirectory.pdf into structured directory entries.

The PDF is an "A to Z Listing By Department" export. Text extraction collapses spaces
inside titles, so titles are re-split on word boundaries for display.
"""

import re

import pdfplumber

SKIP_LINE = re.compile(
    r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)|^A to Z Listing|^\s*$",
    re.IGNORECASE,
)
ROOM_RE = re.compile(r"^[A-Z]{2,4}\d+[A-Za-z]?$")
EXT_RE = re.compile(r"^\d{4,6}$")
NAME_RE = re.compile(r"^([A-Z][A-Za-z''\-]+(?:\s+[A-Z][A-Za-z''\-]+)*),\s*([A-Za-z''\-\.]+(?:\s+[A-Z][a-z]+)?)$")


def split_camel(text):
    """'AdvisingServicesCoordinator' -> 'Advising Services Coordinator'."""
    if not text:
        return ""
    text = text.replace(",", ", ")
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", spaced)
    return re.sub(r"\s+", " ", spaced).strip()


def is_department_header(line):
    if "," in line or " " in line.strip():
        return False
    return bool(re.match(r"^[A-Z][A-Za-z&/\-']{2,}$", line.strip()))


def parse_directory(path):
    entries = []
    current_dept = ""

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for raw in (page.extract_text() or "").splitlines():
                line = raw.strip()
                if not line or SKIP_LINE.match(line):
                    continue

                if is_department_header(line):
                    current_dept = split_camel(line)
                    continue

                tokens = line.split()
                if not tokens or "," not in tokens[0]:
                    # Wrapped continuation of the previous title.
                    if entries and len(tokens) <= 4 and not EXT_RE.match(tokens[0]):
                        entries[-1]["title"] = (
                            entries[-1]["title"] + " " + split_camel(line)
                        ).strip()
                    continue

                phone_ext = ""
                room = ""
                rest = tokens[1:]

                if rest and EXT_RE.match(rest[-1]):
                    phone_ext = rest.pop()
                if rest and ROOM_RE.match(rest[-1]):
                    room = rest.pop()

                name_part = tokens[0]
                last, _, first = name_part.partition(",")

                entries.append(
                    {
                        "last_name": last.strip(),
                        "first_name": first.strip(),
                        "title": split_camel(" ".join(rest)),
                        "room": room,
                        "phone_ext": phone_ext,
                        "department": current_dept,
                    }
                )

    return entries


if __name__ == "__main__":
    import collections
    import json
    import sys

    rows = parse_directory(sys.argv[1] if len(sys.argv) > 1 else "SUdirectory.pdf")
    print(f"parsed entries: {len(rows)}")
    print(f"distinct departments: {len({r['department'] for r in rows if r['department']})}")
    with_title = sum(1 for r in rows if r["title"])
    print(f"with title: {with_title} | with room: {sum(1 for r in rows if r['room'])} | with ext: {sum(1 for r in rows if r['phone_ext'])}")
    print("\nsample:")
    for r in rows[:6]:
        print("  ", json.dumps(r))
    print("\ntop departments:")
    for d, n in collections.Counter(r["department"] for r in rows).most_common(8):
        print(f"   {d}: {n}")
