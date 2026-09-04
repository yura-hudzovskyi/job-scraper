"""Which language a document is written in, decided from its alphabet.

Deliberately not a general-purpose language identifier, and the difference
matters. This distinguishes the four languages this corpus actually contains —
Ukrainian, Russian, English and Polish — by counting characters that only one of
them uses. It cannot tell English from German, and it does not pretend to: a
document it cannot place returns None rather than a plausible-looking guess.

Why not a library: `langdetect` is non-deterministic unless seeded, and the same
vacancy must not detect differently on a re-parse (that is a Phase 2 definition
of done). `lingua` and `langid` are large, and this service's image is
deliberately a plain Python web app with no model weights in it. Character
profiling gets the four languages in the corpus right, deterministically, in a
few lines a reader can check.

When a fifth language appears, or when English needs telling apart from another
Latin-script language, this is the module to replace — the signature is one
function returning an ISO 639-1 code, so a real detector drops in behind it.
"""

from dataclasses import dataclass

# Letters that appear in exactly one of the two Cyrillic languages we see.
_UKRAINIAN_ONLY = frozenset("іїєґІЇЄҐ")
_RUSSIAN_ONLY = frozenset("ыэъёЫЭЪЁ")
# Polish diacritics. `ó` is excluded on purpose: it is also common in other Latin
# scripts, and the remaining eight are enough to identify Polish on their own.
_POLISH_ONLY = frozenset("ąćęłńśźżĄĆĘŁŃŚŹŻ")

# Below this many letters there is nothing to profile — "Python 3.12, AWS" is not
# evidence of any language, and calling it English because it is Latin would put
# a wrong `language_code` on half the short documents in the corpus.
MIN_LETTERS = 20

# The share of a script's letters that must be language-exclusive before that
# language is named. Measured on the fixtures in the tests: genuine text runs
# 9.6% (uk), 7.7% (ru), 4.6% (pl), while noise — one Polish place name quoted in
# an English vacancy, one stray `ё` in a Ukrainian one — runs under 1.1%. Two
# percent sits in that gap with margin on both sides.
#
# The tight case is Polish, whose 4.6% is the lowest genuine signal: a short
# Polish sentence that happens to use no diacritics at all is indistinguishable
# from English here, and comes back "en". Accepted, because the alternative is
# lowering the floor into the noise band and mislabelling English vacancies.
MIN_MARKER_SHARE = 0.02


@dataclass(frozen=True)
class ScriptProfile:
    """What the character counts say, before any language is named.

    Exposed because it is the useful thing to log when a detection looks wrong:
    "1,400 Latin, 12 Cyrillic, 0 Ukrainian markers" explains the answer in a way
    the answer alone does not.
    """

    letters: int
    cyrillic: int
    latin: int
    ukrainian_markers: int
    russian_markers: int
    polish_markers: int


def profile(text: str) -> ScriptProfile:
    letters = cyrillic = latin = 0
    ukrainian = russian = polish = 0

    for character in text:
        if not character.isalpha():
            continue
        letters += 1
        if "Ѐ" <= character <= "ӿ":
            cyrillic += 1
            if character in _UKRAINIAN_ONLY:
                ukrainian += 1
            elif character in _RUSSIAN_ONLY:
                russian += 1
        elif character.isascii() or character in _POLISH_ONLY:
            latin += 1
            if character in _POLISH_ONLY:
                polish += 1

    return ScriptProfile(
        letters=letters,
        cyrillic=cyrillic,
        latin=latin,
        ukrainian_markers=ukrainian,
        russian_markers=russian,
        polish_markers=polish,
    )


def detect_language(text: str) -> str | None:
    """An ISO 639-1 code, or None when the text does not say.

    Mixed documents — a Ukrainian CV listing English framework names — resolve to
    the script that carries most of the letters, which is the language a reader
    would say the document is in. The minority script is not lost: it is still in
    the text, and the lexical channel (spec 11.2) indexes such documents under
    both their detected language and `simple` for exactly this reason.
    """
    counts = profile(text)
    if counts.letters < MIN_LETTERS:
        return None

    if counts.cyrillic > counts.latin:
        return _decide(
            counts.ukrainian_markers, counts.russian_markers, counts.cyrillic, "uk", "ru"
        )
    if counts.latin > 0:
        if counts.polish_markers / counts.latin >= MIN_MARKER_SHARE:
            return "pl"
        # Latin script with no Polish diacritics. In this corpus that is English;
        # it would also be German, and this function has no way to tell. See the
        # module docstring before trusting it beyond these four languages.
        return "en"
    return None


def _decide(
    first_markers: int, second_markers: int, total: int, first: str, second: str
) -> str | None:
    """Pick between two languages sharing a script, by whichever set of
    exclusive letters clears the noise floor. Both or neither means the text is
    genuinely ambiguous, and None is the honest answer."""
    first_clears = first_markers / total >= MIN_MARKER_SHARE
    second_clears = second_markers / total >= MIN_MARKER_SHARE
    if first_clears and not second_clears:
        return first
    if second_clears and not first_clears:
        return second
    if first_clears and second_clears:
        return first if first_markers >= second_markers else second
    return None
