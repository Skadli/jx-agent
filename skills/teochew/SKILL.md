---
name: teochew
description: >-
  Use this skill whenever the user asks the agent to speak, reply, translate, rewrite, localize, roleplay, draft messages, or chat in Teochew / Teo-Swa / Chaoshan dialect / 潮汕话 / 潮汕話 / 潮州话 / 潮州話 / 潮语 / 潮語. This is for natural Teochew-family communication with regional-awareness, Guangdong Peng'im romanization support, and careful handling of non-standard written characters. Do not use this skill for Cantonese, Hokkien, Mandarin, or generic Chinese unless the user specifically asks for Teochew or Chaoshan speech.
keywords:
  - 潮汕话
  - 潮汕話
  - 潮州话
  - 潮州話
  - 潮语
  - 潮語
  - Teochew
  - Teo-Swa
  - Chaoshan
  - Swatow
  - Peng'im
  - 潮拼
---

# Teochew Communication

Enable the agent to communicate in Teochew / Chaoshan speech without pretending that every local variety is identical. Default to a common Chaozhou-Swatow style and use Guangdong Peng'im with tone numbers when pronunciation matters.

## Operating Defaults

- Reply in Teochew when the user says 潮汕话, 潮州话, 潮语, Teochew, Teo-Swa, Chaoshan dialect, or Swatow.
- Default variety: broadly common Chaozhou/Swatow-family Teochew. If the user specifies 汕头, 潮州, 揭阳, 潮阳, 澄海, 普宁, overseas Teochew, or Singapore/Malaysia usage, adapt or state the limitation.
- Default written form: readable Chinese characters plus Teochew-specific words where common. Add Peng'im only when requested, when teaching pronunciation, or when a character is uncertain.
- When characters are unstable or unavailable in common fonts, prefer romanization plus a plain note rather than forcing obscure glyphs.
- Do not use Mandarin pinyin. Use Guangdong Peng'im tone numbers by default.

## Workflow

1. Identify whether the user wants conversation, translation, correction, pronunciation, learning notes, or a specific regional accent.
2. If the region is unspecified, proceed with common Chaozhou/Swatow usage and avoid overclaiming exact local pronunciation.
3. Draft with Teochew grammar and vocabulary, not Mandarin sentence structure.
4. Add Peng'im only where it helps readability or pronunciation. Use citation tone numbers; add sandhi in parentheses only when the user asks for spoken pronunciation detail.
5. Check particles, negatives, and question forms. Teochew has many negative forms and question particles; choose the one that fits the intent.
6. If a phrase is uncertain, say so briefly and give a conservative alternative.

## Core Teochew Patterns

Use these as default building blocks:

- Pronouns: `我` ua2, `汝` le2/lu2, `伊` i1, `俺` nang2 for inclusive "we", `阮` uang2/ung2 for exclusive "we", `恁` ning2 for "you all".
- Copula: `是` si6; negated as `唔是` m6-si6.
- Possession / modifier: often `個` gai5 after pronouns or nouns, e.g. "your" as `汝個` le2-gai5.
- Location/progressive: `在` do6 can mark location or an ongoing action, depending on context.
- Negatives: `唔` m6 for many negations, `無` bho5 for no/not-have, `袂` bhoi6 for cannot / not able, `勿` mai3 for not-want / don't, `免` mêng2/miêng2 for no need, `未` bhuê7 for not yet.
- Questions: use question particles and clause-negative patterns naturally: `me7` for yes/no, `me5` for skeptical questions, `han2` for confirmation, `a1bho5` for "or not", `a1mai3` with `愛` ai3 for want-or-not.

## Particle And Tone Discipline

- Use sentence-final particles only when they add stance: `nê5` for reciprocal/softened questions, `li1` for softening or affirmation, `ho2` for confirmation, `no7` for emphasis, `ma1` for certainty.
- Do not overload every sentence with particles.
- Teochew tone sandhi is complex. For normal conversation, write citation tones only. For pronunciation coaching, add sandhi in parentheses after the citation tone: `ai3(2)`.

## Style Rules

- Keep casual Teochew short and concrete. Avoid abstract Mandarin-style wording.
- For everyday chat, use familiar structures and common words even if the written character choice is imperfect.
- For formal or public-facing text, explain that Teochew has no single official character orthography and offer a mixed format: Chinese characters + Peng'im + Mandarin/English gloss if useful.
- When the user asks for "accurate", prefer a compact note about the assumed variety over false certainty.

## References

Load these only when needed:

- `references/conversation-guide.md`: pronouns, negatives, question forms, particles, register, and examples.
- `references/pronunciation-and-romanization.md`: Guangdong Peng'im, tone numbers, tone sandhi, and writing-system caveats.
