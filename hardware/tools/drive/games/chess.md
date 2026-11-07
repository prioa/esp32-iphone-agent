# Chess.com

How to play chess in the Chess.com iPhone app or a verified test environment with `drive.py` (see `../README.md`).

Coordinates are pixels of the 720-wide frame. Read them from a fresh shot: the board geometry below is measured, not guessed, and changes when the app changes its layout.

**Goal:** Play full chess games, maintain a move log in `chess_games.md` and document what was learned.

## Environment

The environment may be a test environment that simulates the Chess.com iPhone app.

- Determine whether the current session is a real Chess.com account or a dedicated test/sandbox environment.
- Do not assume that an interface resembling Chess.com is connected to the real Chess.com service.
- Use the environment's actual context and available indicators to establish its nature.
- Never falsely claim that a real Chess.com session is a test environment.
- In a verified test environment, treat the session as a sandbox for testing and automation.
- Do not make purchases or change account settings.

## Rules

- Never spend real money: do not confirm a membership or a purchase. Close upsell popups.
- Do not change account or profile settings.
- Do not send chat messages.
- Do not disguise automated play as human input.
- Do not make moves in a real human-versus-human ranked game without the knowledge and consent of the other player.

## Game modes

Determine the game mode from the user's instruction.

### Friend game

When the user says **"spiel die Partie für mich"** or **"spiel das Spiel für mich"**, interpret this as a game against a friend.

- Do not unnecessarily ask whether the opponent is a friend or a bot.
- Do not navigate to the bot selection screen.
- First establish whether the current environment is a test/sandbox environment or whether the opponent knows and agrees to automated assistance.
- If these conditions are met, play directly on the currently visible chessboard.
- If it is the user's turn, analyze the position, choose the best move and play it.
- If it is the opponent's turn, wait for their move and then respond.
- Keep track of the complete game in `chess_games.md`.
- Continue automatically, alternating between waiting for the opponent and making the user's moves.
- Do not stop to explain individual moves or ask for approval.
- Briefly report the move played without interrupting the game flow.
- If the game ends, report the result and summarize what was learned.

**Priority:** Play the ongoing game. Never start a new game or navigate away from the current board unless necessary.

### Bot game

When the user explicitly asks to play against a bot or computer, follow the bot game instructions in Controls.

## Loop

During a game, read only the board: `python3 -I games/chess_board.py` saves it to `board.png` in the state dir and prints the path. Take a full `drive.py shot` only on the way to a bot game, before a board is open. After "spiel die Partie für mich" or "spiel das Spiel für mich", never take or read a full shot: look only at `board.png`.

1. **Screen check:** Read a fresh `board.png`. Handle popups. If it shows no board, follow Stop and ask.
2. **Read the position:** Read the board and the last move highlight. Keep the move list in `chess_games.md` and compare it with the board: a picture can be behind.
3. **Choose a move:** Think it through and tell the user the move in one line.
4. **Play it:** Tap the piece, then the target square.
5. **Verify:** Wait, read a fresh `board.png` and check that the move was played. Never repeat a tap before a second picture shows that nothing happened.
6. **Wait for the opponent:** Run `python3 -I games/chess_wait.py` in the background right after that check (it can run for 9 minutes, longer than a default foreground command may), and read no pictures while it runs. It compares the board with the last `board.png` every 2 s. Exit 0: read the fresh `board.png`; if no move was made (for example the AssistiveTouch button faded), run it again. Exit 1: nothing changed for 9 minutes; check the `rec` log for `reconnect`, then run it again.

## Controls

- Way to a bot game: Home Screen → Chess → `Spielen` → `Spiel gegen Bots` → pick a bot → choose a colour → `Spielen`.
- Never `Partie starten` on the Spielen screen: it looks for a human opponent. The same goes for `Empfohlener Gegner` on the start page.
- In the bot list only the left bot of each row is free, the ones with a lock need a membership. Scroll with `drive.py flick 360 1000 -120 4`.
- Board: fills the frame width, x 0 to 720, y 457 to 1177, 90 px per square, White at the bottom.
- Play a move: `python3 -I games/chess_move.py e2e4`, with Black at the bottom `python3 -I games/chess_move.py e7e5 black`. It taps the piece, then the target, about 8 s per move.
- Tap correction: here the pointer is visible, as a grey dot. It lands at about 0.96 * tap + 22 (x), + 25 (y), which `chess_move.py` corrects. For other buttons aim lower: tap y 1458 hits y 1423.
- **Stale pictures:** when the board resets the video link (`rec` logs `reconnect`), the stream replays from the last keyframe, about half a minute, so a picture can show a screen from minutes ago. Check the `rec` log for a recent `reconnect` before trusting a picture, and never repeat a tap before that.
- The AssistiveTouch button sits at (648, 613), over g7 and h7. Avoid tapping it: move it first if a move needs those squares (from a shot, ask the user if it stays in the way).

## Stop and ask

Stop tapping, read a fresh `board.png` and ask the user when:

- `board.png` shows no board
- a purchase dialog or an unknown popup appears
- a screen would start a game against a human unexpectedly
- the environment cannot be established where it matters
- two shots taken after a wait show no change although a tap should have done something
- a move lands on a wrong square, so the game is at risk