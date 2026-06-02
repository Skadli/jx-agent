---
name: cantonese
description: >-
  Use this skill whenever the user asks the agent to speak, reply, chat, translate, rewrite, localize, roleplay, draft messages, or produce copy in Cantonese / Yue / 粤语 / 粵語 / 廣東話 / 广东话 / 香港粵語. This is for natural Cantonese communication, especially Hong Kong-style colloquial written Cantonese, with Jyutping pronunciation support when requested. Do not use this skill for ordinary Mandarin, Standard Written Chinese, or generic Chinese unless the user specifically asks for Cantonese.
keywords:
  - 粤语
  - 粵語
  - 廣東話
  - 广东话
  - 香港粵語
  - Cantonese
  - Yue
  - Jyutping
  - 粵拼
  - 港式
---

# Cantonese Communication

Enable the agent to communicate in natural Cantonese instead of Mandarin-with-Cantonese-words. Default to modern Hong Kong colloquial written Cantonese in Traditional Chinese unless the user asks for another region, Simplified Chinese, romanization, or a learning format.

## Operating Defaults

- Reply in Cantonese when the user asks for 粵語 / 廣東話 / Cantonese, even if the rest of the prompt is Mandarin or English.
- Use Traditional Chinese by default: `我`, `你`, `佢`, `係`, `唔`, `冇`, `嘅`, `咗`, `喺`, `啲`, `咁`, `呢`, `嗰`.
- Add Jyutping only when the user asks for pronunciation, learning help, romanization, tone marks, or "怎么读". Keep it compact: `你今日點呀？nei5 gam1 jat6 dim2 aa3?`.
- Keep English names, code terms, API names, commands, and brand names unchanged unless the user asks for localization.
- Use Standard Written Chinese only when the task is formal, official, legal, academic, or explicitly says 書面語 / 正式中文.

## Workflow

1. Identify the user's intent: direct chat, translation, rewrite, explanation, roleplay, learning aid, or pronunciation.
2. Choose register: casual chat, polite service tone, friendly text message, professional but colloquial, or formal Standard Written Chinese if requested.
3. Draft in Cantonese grammar, not by replacing Mandarin words one by one.
4. Add sentence-final particles only when they carry tone or intent. Do not sprinkle particles randomly.
5. Check for Mandarin leakage: replace `的/了/在/没有/是不是/我们/他们` with the Cantonese structure that fits the context.
6. If uncertain about a phrase, prefer a simpler natural Cantonese sentence over an over-specific expression.

## Core Cantonese Patterns

Use these as default building blocks:

- Pronouns: `我`, `你`, `佢`, `我哋`, `你哋`, `佢哋`.
- Copula and negation: `係`, `唔係`, `唔`, `冇`, `未`.
- Location and possession: `喺`, `嘅`, `有`, `冇`.
- Aspect: put markers after the verb: `做咗`, `做緊`, `做過`.
- Questions: `係咪`, `有冇`, `未`, `點解`, `幾時`, `邊個`, `咩`.
- Common softeners: `唔該`, `麻煩你`, `我想問`, `可唔可以`, `得唔得`.

## Particles

Use particles sparingly and intentionally:

- `呀`: softens statements or questions: `你想我點幫你呀？`
- `啦`: suggestion, transition, or settled outcome: `咁我哋就咁做啦。`
- `喎`: adds reported/new/contrastive information: `佢話今日唔得喎。`
- `咩`: skeptical or surprised question: `真係咁咩？`
- `㗎` / `㗎喇`: explanatory or assertive ending: `呢個係正常㗎。`
- `囉`: obvious, resigned, or "that's how it is": `而家唯有等囉。`
- `啫`: "only / just", often softening: `我只係提一提啫。`

## Style Rules

- Keep casual replies short and direct. Cantonese often sounds more natural with fewer abstract nouns and more concrete phrasing.
- Use local conversational rhythm: `我睇咗一下`, `呢個位`, `咁樣會順啲`, `你可以試吓`.
- Preserve the user's stance. If they sound annoyed, do not over-cheer; if they want concise output, do not add teaching notes.
- For translations, preserve meaning first, then idiomatic Cantonese. Literal translation is a last resort.
- For educational output, include a short note about register or particle choice after the Cantonese answer.

## References

Load these only when needed:

- `references/conversation-guide.md`: core vocabulary, grammar, particles, examples, and anti-patterns.
- `references/pronunciation-and-romanization.md`: Jyutping, tone numbers, and pronunciation output format.
