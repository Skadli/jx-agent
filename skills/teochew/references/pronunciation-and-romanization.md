# Teochew Pronunciation And Romanization

Use this file when the user asks for pronunciation, romanization, tones, tone sandhi, or exact reading.

## Default Romanization

Use Guangdong Peng'im by default. It is keyboard-friendly and widely used in mainland references. Write tone numbers after syllables:

- `ua2` = 我
- `le2` / `lu2` = 汝
- `i1` = 伊
- `nang2` = 俺
- `m6-si6` = 唔是

Avoid Mandarin Hanyu Pinyin for Teochew.

## Tone Numbers

Use 1-8 tone numbers. A practical reference:

| Tone | Traditional class | Contour guide |
| --- | --- | --- |
| 1 | 陰平 | mid level |
| 2 | 陰上 | high falling |
| 3 | 陰去 | low falling-rising |
| 4 | 陰入 | low checked |
| 5 | 陽平 | high level |
| 6 | 陽上 | rising |
| 7 | 陽去 | low level |
| 8 | 陽入 | high checked |

Checked tones occur in syllables with stop endings or glottal-stop-like endings.

## Tone Sandhi

Teochew tone sandhi affects the preceding syllable in a word or phrase. For normal output, citation tones are enough. For learning output, show changed tones in parentheses:

- `ai3(2)` means citation tone 3, pronounced with the sandhi value 2 in that phrase.
- Do not over-annotate sandhi if the user only wants a fluent chat reply.
- If the phrase boundary is unclear, do not guess aggressively; note that actual sandhi depends on phrasing and local speech.

## Character Orthography

Teochew does not have a single official character standard comparable to modern written Cantonese. Many common spoken words have multiple character choices or hard-to-type variant characters. Use this policy:

- Use common readable characters when they are stable: `我`, `汝`, `伊`, `唔`, `無`, `未`, `免`, `愛`, `食`, `個`.
- Use romanization when the character is uncertain or font support is poor.
- If the user needs a polished public text, offer three columns: characters, Peng'im, and meaning.

## Regional Variation

Local varieties differ across Chaozhou, Swatow/Shantou, Gêg-ion/Jieyang, Têng-hai/Chenghai, Chaoyang, Puning, and diaspora communities. If the user needs exact local accent, ask which place or community to follow.

## Sources Consulted

- Learn Teochew pronunciation, tones, sandhi, orthography, and regional differences: https://learnteochew.com/pages/pronunciation.html
- Teochew phonology tutorial: https://kahaani.github.io/gatian/
- Learn Teochew romanization tools and guide: https://learnteochew.com/
