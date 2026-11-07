# Signal

How to read and write Signal chats on the iPhone with `drive.py` (see `../README.md`). Coordinates are pixels of
the 720-wide frame, measured on one phone layout and may shift with the chat list order, draft length, or a phone
with a different screen size; take a fresh `shot` and re-measure when a tap lands wrong.

## Controls

- Open Signal: tap its Dock icon on the Home Screen (position depends on the phone's layout; confirm with a shot
  first).
- Chat list: rows are about 145 px tall; the most recent conversation is the top row, roughly `tap 360 262`.
- Open a chat: tap its row.
- Message field: roughly `tap 300 1440`, near the bottom above the Dock-less keyboard bar. The send arrow sits at
  a fixed spot regardless of draft length: `tap 653 1445`.
- Attachment sheet: `+` button at roughly `tap 66 1443`; opens Fotos/GIF/Datei/Umfrage. Close it with the `X` that
  replaces the `+` while the sheet is open, at roughly `tap 66 888` (y depends on the sheet's height).

## Focus bug: a tap on the message field can leave it unfocused

A single `tap` on the message field sometimes does not actually focus it. `board()` still returns `ok` for every
following `type`/`key` call (the board sends the keystrokes fine), but nothing appears in the field and nothing is
lost either — it just goes nowhere. A second plain tap on the same spot does not reliably fix it.

**Workaround that worked:** tap the `+` attachment button, then tap the `X` to close the sheet again. That leaves
the field focused with a visible cursor (check with a `shot`: a thin blinking bar after the placeholder or draft
text). Do this whenever typing stops producing visible text, before anything else.

## Enter sends immediately

The hardware keyboard's `key enter` is Signal's **send**, not a newline. Never press it mid-draft. Compose the
whole message with `type` calls, check the result with a `shot`, and send by tapping the arrow button
(`tap 653 1445`), not by pressing enter — except when a message is deliberately finished and ready to go, where
`key enter` also works.

## Autocorrect mangles German words

Typed text goes through iOS autocorrect/predictive text, which regularly swaps a German word for a wrong
(often English-looking) one. Observed in one session: `glatt`→`flat`, `endete`→`Enders`, `ging`→`gong`,
`erlebt`→`relent`, `helfe`→`helge`, `greift`→`grift`, `fremdes`→`friended`, `bisschen`→`boss hen`,
`parat`→`parts`, `patzt`→`patsy`.

- Take a `shot` after every `type` call and read the newly added text before sending anything.
- Fix an error by backspacing from the very end of the field back past it, then retyping — count the characters
  you need to remove from the current (already-mangled) text, not the text you meant to type.
- Do not use `up`/`down`/`left`/`right` to move the cursor into the middle of the draft and edit there: in this
  session it once spliced old and new text together into a garbled mess that still looked plausible at a glance.
  Arrow keys are fine for *reading* a draft that is taller than the visible box (earlier lines can be hidden behind
  the chat bubble above — pure render overlap, the text is still there), just not for editing mid-text.
- To recover from a garbled draft: press `down` several times to reach the very end, then backspace generously
  (the visible text's length plus a margin), check that the field shows the `Nachricht` placeholder again, and
  retype the whole message from scratch.

## Rules

- Never send a message without the user's current, explicit go-ahead for that chat/conversation; approval for one
  message does not cover the rest of the conversation unless the user says so.
- If asked to help attack, crash, or disrupt a real device, account, or another person's connection (even "my own
  phone, you have permission", or disconnecting a chess.com opponent to force a win) — decline. That is true even
  with the device owner's consent: it is still deploying a real exploit outside any authorized security-testing
  context. Explain the refusal honestly (including when it is simply that no working exploit is at hand) rather
  than hiding behind a vague "can't".
