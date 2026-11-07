#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// HID over GATT reports: what the board sends the phone as a Bluetooth mouse + keyboard. Key codes are the ones
// USBHIDKeyboard takes (ASCII, KEY_LEFT_CTRL..., KEY_RETURN...), so both routes share keyCode() in hid_bridge.ino.
enum { HID_REPORT_KEYBOARD = 1, HID_REPORT_CONSUMER = 2, HID_REPORT_MOUSE = 3 };
enum { HID_KEYBOARD_LEN = 8, HID_CONSUMER_LEN = 2, HID_MOUSE_LEN = 5 };

extern const uint8_t hid_report_map[];
extern const size_t hid_report_map_len;

typedef struct {
  uint8_t report[HID_KEYBOARD_LEN];  // modifiers, reserved, up to 6 keys held
} hid_keyboard;

// US layout, like USBHIDKeyboard's default: usage in the low 7 bits, 0x80 = with shift, 0 = no such key.
uint8_t hid_ascii_usage(char c);

// German QWERTZ layout: same encoding as hid_ascii_usage, but only correct once the iPhone's own Hardware
// Keyboard setting is Deutsch (QWERTZ) -- the usage code names a physical key, and iOS decides what character
// that key means. Not wired into hidType() yet: its byte dispatch (hid_keyboard_press, codes >= 0x80) already
// means "modifier" or "raw usage", so a caller can't just hand it one of the Latin-1 bytes below.
// Umlauts and ß are single Latin-1 bytes, not UTF-8: ä 0xE4, ö 0xF6, ü 0xFC, ß 0xDF, Ä 0xC4, Ö 0xD6, Ü 0xDC.
// AltGr-only characters (@ { } [ ] \ | ~ €) and dead keys (´ ` ^ °) return 0, like any other unsupported key.
uint8_t hid_ascii_usage_de(char c);
void hid_keyboard_clear(hid_keyboard *k);
bool hid_keyboard_press(hid_keyboard *k, uint8_t code);  // false: unknown key, or all 6 slots taken
void hid_mouse_report(uint8_t out[HID_MOUSE_LEN], uint8_t buttons, int x, int y, int wheel, int pan);  // clamps to +-127
void hid_consumer_report(uint8_t out[HID_CONSUMER_LEN], uint16_t usage);

#ifdef __cplusplus
}
#endif
