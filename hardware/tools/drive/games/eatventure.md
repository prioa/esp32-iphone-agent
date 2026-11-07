# Eatventure

How to play Eatventure on the iPhone with `drive.py` (see `../README.md`). Coordinates are pixels of the 720-wide
frame and depend on the scroll position, so read them from a fresh shot.

**Goal:** reach the next restaurant. The button at the bottom left shows a build icon, and the plane (`Fliegen`) as the
last stage of a restaurant. Whenever the build icon is active (red arrow on it), use it right after the crates and new stations. Fly only when
every station is at its max level. Opening crates always comes first, buying new stations second. Until then unlock
every station and bring all of them to their max level as fast as possible, with every crate opened.

## Fast path: let the bot play

Do not click by hand when `eatventure_bot.py` can. Measured 2026-11-06 in a fresh pasta/juice restaurant: 10 minutes
gave 920 station clicks, 34 upgrade-menu buys, 10 crates, 1 slot and 13 of 19 station levels maxed, with no model in the loop.

1. Start the game, close `Offline-Einnahmen` with its X (never `Sammeln x2`, it plays an ad), check with
   `python3 -I games/eatventure_bot.py --check <png>` that it sees arrows and stations.
2. Run `python3 -I games/eatventure_bot.py --minutes 30` in the background and read nothing while it runs. Its log
   (`bot.log` in the state dir if you redirect it) has one line per action. Exit 0: time is up, look at the screen and
   start it again. Exit 2: it saved `bot_stop.png`, look at that.
3. Click cadence: 0.02 s after each click is the fastest (6.1 levels/s), 0.2 s is four times slower. The bot picks it by itself.
4. Only step in for what the bot stops on: the fly dialog with an ad or gem symbol, unknown popups, events.
5. **Fly as soon as every station is at max** (`25/25 maxed` in the log): no extra idle sweep. A restaurant took about
   20 minutes this way (first one, 2026-11-06: 04:58 to 05:20). A round takes about 7 minutes, so waiting for one more
   sweep costs a third of that. Crates and empty slots still come first.
6. **Flying by hand:** if the bot's tap on the plane misses (a grey pointer dot below the button in the picture), use
   `ESP32_HOME_EVERY=1 python3 -I drive.py tap 67 1400`: the dialog `Fliege nach <city>` opens, the blue `Fliegen`
   (360, 1085) has no gem or ad symbol. Then `OK` (360, 972) on `Willkommen in <city>` and the blue `Eröffnen`
   (360, 916) on the first stand. Two taps at y 1450 and 1458 missed because the pointer drifted low.
   Restaurant 2 is Sao Paulo, an ice cream stand with one dashed slot and an arrow.

## Scrolling (tested 2026-11-06)

- Most scrolls failed until the firmware fix of 2026-11-06 (07:00): serial commands run as the "app", and the
  firmware's guard for a crashed app (`app && !ble_app_alive()`, `loop()` in `hid_bridge.ino`) let go of every press
  before its first move, because `ble_app_connected()` counts the phone's own mouse link. A press from USB serial is now
  exempt (and let go after 5 s without a serial command). Since then 12 of 12 test gestures moved the restaurant,
  short ones (`alt1`) and long ones (`d300`).
- Everything learned before that fix about which gesture works (pauses, pieces, lengths, the mouse wheel) was measured
  through that bug and does not hold. The mouse wheel (`scroll 30 0`) still does nothing in this game.
- The walk uses `WALK_SCROLL` from `eatventure_bot.py` (`alt2` = `alt1` twice); pick a longer one (`d300` to `d800`)
  from the scroll test buttons on the web page and set `EATVENTURE_SCROLL`.
- The bot checks the picture after each scroll, waiting up to 8 s more for it: no change then means the end, without a
  second scroll (user, 2026-11-06). The bottom
  end is also recognised by the plain grey strip above the bar.
- The firmware also has `burst 5,10,20,...` (one report per number, up to 127 counts, 15 ms apart) for swipes that
  speed up like a finger (`f1` to `f5`); not retested after the fix.
- Mirroring can freeze when the picture changes a lot: `cmd status` shows `ovf` and `fz`, `rec` logs `reconnect`.
  Keep `fps 2`, restart Screen Mirroring on the iPhone to recover.

## Full passes and flying

- The whole restaurant is walked only after a menu purchase of a waiter, table, cook or cashier whose crate is not in
  view (user's rule, 2026-11-06). The bot counts what it bought (`+1 Koch`, `+2 ...`, a table by `Tisch` in its title:
  one crate), takes off every crate it opens, and walks up and back down only for what is missing, until the count
  reaches 0. It buys such a row with one tap and counts it when the top row changes.
  Cashiers do drop crates, cars (`+1 Auto`) do not (user, 2026-11-06).
- The `crates` button on the page always walks it all. A scroll without movement counts as
  the end. The log says `reached the bottom of the restaurant (no change after a scroll)` or `the top was NOT reached
  after 40 scrolls`.
- Flying: the plane is only active once every station is at max (user, 2026-11-06), so the bot flies as soon as it is,
  with no pass through the restaurant first. A dialog button with a gem or ad symbol still stops it.

## Map of the restaurant

- A flight or a renovation resets everything about the old place (`new_place()`): the map, the station names and levels,
  the known ends, the crates owed. A renovation changes the food on offer too (seen 2026-11-06).

- `eatventure_map.py` stitches a map from the live picture and shows it as a schematic under the live feed on the web
  page: flat colour blocks, a frame for the current view, stations (found by their arrow), crates and empty slots. Names
  and levels come from the bot when it opens a station.
- After each turn the bot reads the price of the station's next upgrade (white on a blue button, red on a grey one, with
  the pointer moved off it) and writes it on the map. A round begins with the cheapest, also if that one is out of view, and
  then goes on in the order of unlocking (user, 2026-11-06). That order is kept per station (`order`, `#3` on the map). The
  bot knows a station's name only once it opened it, so the order is the one it first saw them in. At a stop it does
  everything in view before it scrolls on, so across stops the order holds only roughly.
- At every stop of the stations phase: slots and each station until its button is no longer blue, one after the other,
  and only then the crates in view, also those of new slots and stars (user, 2026-11-06). This replaces the older
  "crates first" rule for this phase.
- The stations phase uses the map first: it stays where the work is, and with nothing in view it scrolls to the crates and the
  stations not at max on the map that are out of view, each at most once a minute (`MAP_REVISIT`).
- While the map does not recognise the picture (state `lost`) its position counts as unknown. The bot then looks further,
  until the map knows the place again or something turns up. It does the same while stations it knows are missing on the
  map. A whole trip without anything: a minute's pause. The bot needs the web page running for the map.
- Only a flight or a renovation clears the map. A scan updates the picture and keeps the stations with their names,
  prices and order.
- The button `Karte erstellen/aktualisieren` starts the bot's `map` phase: one trip to the very top and back
  down, no taps. It runs on that button and once when the bot starts, in either mode (user, 2026-11-06). Otherwise the bot
  walks the restaurant by itself only for crates it cannot find, and the map grows from what it passes anyway.
- A scan sees a station only by its red arrow, so a station that cannot be afforded right then is not on the map yet.
- The game's fixed icons are left out; those parts stay blank until a scroll uncovers them. Positions are in frame pixels.
- A station without an arrow is unknown to the map until it had one. Tables and staff have no symbol of their own: their
  crates show as crates.

## Watch loop (how the bot plays now)

Since 2026-11-06 there are no rounds and no pauses. The bot looks at the screen over and over (at the latest every 3 s)
and does the first thing from this list that the picture offers, then looks again:

1. a crate, 2. an empty slot (unlock), 3. build/fly when the button is active (flying as soon as every station is at max),
4. the upgrades menu when its badge shows an arrow, 5. a station with a red arrow, lowest level first.

A station that had a turn is left alone for 25 s (cooldown), not for a whole round: with money coming in fast, a red arrow
means it is affordable again. When nothing on the visible part is to do, it scrolls on (down, back up) and looks again. A
log line `pass over in N s` marks one trip to both ends, which replaces the old round.

## Loop (by hand, same order)

Repeat these steps in order:

1. **Screen check:** look at the live picture. Handle known popups (see Controls). If the screen is unknown, follow Stop and ask.
   If the button at the bottom left is active, tap it (67, 1430) and press the blue button in its dialog (`Renovieren`
   with a coin price, or `Fliegen` once all stations are maxed). Then confirm what follows (`OK`, `Eröffnen`).
2. **Crates:** open every wooden crate before spending more money. See Crates.
3. **Upgrades menu:** open the arrow at the bottom right and buy what is affordable, from the top of the list. If you
   bought anything, go back to step 2.
4. **Stations:** give the next station its turn. See Station rounds.
5. **Full sweep:** when every station has had its turn, scroll from the top to the very bottom of the restaurant and
   look for crates and red arrows. If you find a crate, go back to step 2.
6. **New round:** clear the list of done stations and go back to step 2.
7. **Nothing to do:** if nothing is affordable and there are no crates, wait 60 s and start again.

## Crates

Crates unlock what was bought or reached: stations, cooks, waiters and tables. Nothing works until its crate is open.

- **New station:** when a station is unlocked or bought, its crate must be opened right away. The station cannot cook
  or be upgraded before that.
- Open crates right after every purchase in the upgrades menu and after every station upgrade that unlocks something.
- New crates appear in the kitchen and up in the dining room, often out of view. Never assume the visible part of the
  restaurant is complete: scroll through all of it.
- A crate cannot be opened while a station's upgrade panel is open: close the panel first (tap free floor), then tap
  the crate.
- The icons on the right edge (party, trophy, events) lie over the restaurant and open menus. A crate partly under
  one: tap its free part, or scroll first.
- **Staff and tables:** buying cashiers, cooks, waiters or tables in the upgrades menu puts new crates into the dining
  room, up to four in a row, and the game may scroll there by itself (the screen jumps, an open panel vanishes). After
  every such purchase scroll through the whole dining room, from the entrance down to the kitchen, and open all of
  them. Crates can hide under customers: tap them anyway.
- **Empty slots:** a dashed empty slot, often with a red arrow, is a station you can unlock. Buying it comes right after
  the crates. Tap it, then tap the price on "Freischalten". Every unlocked station spawns a crate at that place: open
  it right away, before anything else.
- After tapping a crate, wait and look at the screen again to confirm that it is gone.

## Station rounds

Money earned while one station is being upgraded makes it affordable again, so without rounds the agent stays on that
one station. Every station gets one turn per round.

- Keep a list of the stations that are done this round, by food name, because coordinates change with scrolling. Keep
  it across all loop steps until step 6.
- **Unlock first:** if an empty slot is affordable, unlock it before any upgrade. This does not use up a station's turn.
- **Order:** a new station goes first. Then a station close to its next star, if that level is affordable. Then the
  lowest level first.
- **One turn:** buy every level that is affordable at once: click until the button turns grey, without a pause. Then
  add the station to the list. `eatventure_bot.py` plays this whole loop by itself.
- **Grey button:** if the button is already grey, add the station to the list and move on. Do not wait for it.
- **Done means done:** do not touch a listed station again this round, even if it shows a red arrow or looks like the
  best choice. Do not recalculate the choice between turns: take the next station that is not on the list.
- A station at max level stays out of the rounds.

- **Truck icon (left edge, `Essenslieferung`):** the user allowed the bot to use it (2026-11-06). At the start of a
  stations or menu phase, on a red `!`, the bot opens it and presses `Akzeptieren` on the row marked `Bester Deal`, never
  `x2` (an ad). The icon then counts the order (`0/13`) and the game goes on as usual. A dialog with nothing to accept
  is saved as `truck_unknown.png` and closed, and the bot looks again 5 minutes later.
- **Person icon (left edge, `Helfer einladen`):** the user allowed the bot to use it (2026-11-06). On a red `!` the bot
  invites the helper with the most red items, then the highest sum of levels; items below level 4 do not count, and
  nobody is invited if no item counts. `+4m` plays an ad: never. Levels are read with `eatventure_digits.png`. An item it
  cannot read does not count, and the picture is saved as `helper_digits.png`. Grey items are not seen as items.

## Stations

- The stations sit at the very bottom of the restaurant. Keep swiping until the picture stops moving, or some stay out
  of view.
- A red arrow means an upgrade is affordable.
- Five blue stars are not the end: a tier of green stars follows (seen at level 150+). The max level is not known yet.
- Profit jumps at each star.
- Tap the station's food icon, then click the big blue price button.
- Click the button for 5 s, then check that it can still upgrade: `python3 -I games/eatventure_up.py X Y [ROUNDS]`
  does this and stops when the button turns grey or gets covered. Then go on with the next station. Prices grow fast.
  Holding the button buys only one level with the mouse, so it has to be clicks.
- X Y is the centre of the blue price button. Read it from the screen first: a station's panel opens above or below it,
  about y 595 for an upper station and y 1220 for one at the bottom of the screen, x depends on the station.
- The script prints `stop before round N` when the button is grey (not affordable yet) or gone (the game scrolled or a
  popup came up). Look at the screen before doing anything else.
- Stations seen so far: burger, special burger, fries, onion rings, nuggets, chicken pieces, milkshake, hot dog, pizza,
  drinks. New ones appear as the restaurant grows.

## Upgrades menu

- Open it with the arrow at the bottom right (652, 1445). The price buttons are in the right column, the top row is
  at (564, 625). Click it 5 to 10 times: each purchase moves the list up, so the next item is in the same place.
- Do not use `eatventure_up.py` here, its colour check lands under the pointer. Read the list again after a burst.
- The pointer shows in the picture and can hide a button's coin. The bot taps the menu's buttons at x 600, right of
  the coin, and it counts a station as MAX only after moving the pointer off a grey button without a coin.
- Close it with the red X (610, 510). While it is open crates cannot be tapped.
- Staff and table purchases need their crates opened afterwards, see Crates.

## Controls

- Scroll toward the top of the restaurant with `drive.py flick 540 500 120 4` and toward the bottom with
  `drive.py flick 540 900 -120 4`, about 250 px per flick.
- Do not use `drag`: the view follows only the very start of a long drag, then snaps back.
- Layout from the top: road and trees, the entrance window, the dining room (tables and two counters), the kitchen
  with the stations, a lawn at the bottom. Six flicks up from the kitchen reach the road. A full sweep is a few
  flicks per step, looking at the screen after each one.
- Close a panel before scrolling, it swallows swipes. Tap free floor to close it.
- Popups at start: "Während du weg warst" (gems, `Sammeln`), "Offline-Einnahmen" (close with X).
- Read `~/.cache/esp32agent-drive/live.png` directly to see the screen, no `shot` needed (it only copies that file;
  use it to keep several frames for a comparison). ffmpeg replaces the file atomically.
- A tap usually shows in the picture after about 1.5 to 3 s, sometimes 8 s or more: wait before judging it, and take a second shot
  before repeating one.

## Starting the game and a fresh restaurant

- Open the game from the home screen: a tap on the `Suchen` pill does nothing. Swipe down instead
  (`drive.py drag 360 500 360 900`), Spotlight opens with Eatventure among the Siri suggestions, tap its icon.
- A restaurant just reached by flying is nearly empty: bare floor, plain yellow counters, dashed slots. That is normal,
  not an unknown screen. A dashed slot with a red arrow is an unlock: tap it, pay `Freischalten`, open the crate.
  Order that worked: slot, crate, upgrades menu (waiters, customers, tables, cooks), crates again, next slot.
- The icons on the left and right edges (cart, person, truck, 18-box, party, trophy, timed crates) are fixed overlays,
  they do not scroll. Only the floor moves.
- A flick can need 8 s before the picture moves: wait before flicking again.
- Upgrades menu: tap the top row with 3 s between taps (`for` loop), it moves up after each buy. 8 taps in a row bought
  eight upgrades. Staff, table and cook purchases drop crates in the dining room and kitchen.
- Station stars drop crates next to the station (extra pots, cooks). `eatventure_up.py` can stop early because of them:
  close the panel (tap free floor, e.g. 450 1330), open the crates, reopen the station and run the script again.
- A crate partly under the burger icon at the left edge: tap its free part (about x 97).

## Stop and ask

Stop tapping, take a shot and ask the user when:

- every station is at max level and every crate is open
- a purchase dialog or an unknown popup appears: do not confirm anything
- two shots taken after a wait show no change although a tap should have done something
- the game shows a screen not described here, such as an event or adventure
- a build or fly dialog's button shows a gem or an ad symbol (a gold coin price is fine)

## Rules

- Never spend real money: the cart at the top left and the blue plus next to the gems are shops. Do not open them
  without asking the user first.
- Do not spend gems unless the user allows it.
- `Sammeln x2` and `Boost` play an ad. Skip them unless the user wants them.
- Do not play events or adventures unless the user asks for them.