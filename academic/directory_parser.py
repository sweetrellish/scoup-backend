"""Parse SUdirectory.pdf into structured directory entries.

The PDF is an "A to Z Listing By Department" export with a fixed column layout:
department headers are bold 10pt, entries are 8pt with name/title/room/extension
in fixed x-ranges. Text extraction collapses spaces inside titles, so titles are
re-split for display.
"""

import re

import pdfplumber

# Column boundaries measured from the PDF layout.
X_TITLE = 150
X_ROOM = 415
X_EXT = 505

_JOINERS = ("forthe", "ofthe", "andthe", "inthe", "atthe", "onthe", "tothe")


def split_camel(text):
    """'AdvisingServicesCoordinatorfortheFulton' -> 'Advising Services Coordinator for the Fulton'."""
    if not text:
        return ""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    for joiner in _JOINERS:
        spaced = re.sub(
            r"(?<=[a-z])" + joiner + r"\b",
            " " + joiner[:-3] + " the",
            spaced,
            flags=re.IGNORECASE,
        )
    spaced = re.sub(r"(?<=[a-z])(for|of|and|in|at|to)\b", r" \1", spaced)
    spaced = spaced.replace(",", ", ")
    return re.sub(r"\s+", " ", spaced).strip()


def _line_key(word):
    return round(word["top"], 1)


def parse_directory(path):
    entries = []
    current_dept = ""
    _stop = False

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            if _stop:
                break
            words = page.extract_words(extra_attrs=["fontname", "size"])

            lines = {}
            for word in words:
                lines.setdefault(_line_key(word), []).append(word)

            for top in sorted(lines):
                row = sorted(lines[top], key=lambda w: w["x0"])
                bold = [w for w in row if "Bold" in w["fontname"]]

                # The PDF repeats its 12pt title where the by-department section ends and an
                # alphabetical section begins; that second section has no department headers.
                if bold and bold[0]["size"] > 11.0 and entries:
                    _stop = True
                    break

                # Bold 10pt rows are department headings.
                if bold and abs(bold[0]["size"] - 10.0) < 0.6:
                    current_dept = split_camel("".join(w["text"] for w in bold))
                    continue
                if bold:
                    continue

                name_col = [w["text"] for w in row if w["x0"] < X_TITLE]
                title_col = [w["text"] for w in row if X_TITLE <= w["x0"] < X_ROOM]
                room_col = [w["text"] for w in row if X_ROOM <= w["x0"] < X_EXT]
                ext_col = [w["text"] for w in row if w["x0"] >= X_EXT]

                name_text = " ".join(name_col).strip()

                # A row with no name is a wrapped continuation of the previous title.
                if not name_text or "," not in name_text:
                    if entries and title_col:
                        entries[-1]["title"] = (
                            entries[-1]["title"] + " " + split_camel(" ".join(title_col))
                        ).strip()
                    continue

                if name_text.lower().startswith(("monday", "tuesday", "wednesday",
                                                 "thursday", "friday", "saturday", "sunday")):
                    continue

                last, _, first = name_text.partition(",")
                entries.append(
                    {
                        "last_name": last.strip(),
                        "first_name": first.strip(),
                        "title": split_camel(" ".join(title_col)),
                        "room": " ".join(room_col).strip(),
                        "phone_ext": " ".join(ext_col).strip(),
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
    print(f"with title: {sum(1 for r in rows if r['title'])} | room: {sum(1 for r in rows if r['room'])} | ext: {sum(1 for r in rows if r['phone_ext'])}")
    print("\nsample:")
    for r in rows[:5]:
        print("  ", json.dumps(r))
    print("\ntop departments:")
    for d, n in collections.Counter(r["department"] for r in rows).most_common(6):
        print(f"   {d}: {n}")
