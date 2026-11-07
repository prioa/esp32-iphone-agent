#include <stdbool.h>
#include <string.h>
#include "hid_report.h"

const uint8_t hid_report_map[] = {
  // Keyboard, report 1: modifiers, reserved, 6 keys; LEDs as output
  0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, HID_REPORT_KEYBOARD,
  0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
  0x95, 0x01, 0x75, 0x08, 0x81, 0x01,
  0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02,
  0x95, 0x01, 0x75, 0x03, 0x91, 0x01,
  0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x73, 0x05, 0x07, 0x19, 0x00, 0x29, 0x73, 0x81, 0x00,
  0xC0,
  // Consumer control, report 2: one 16-bit usage (AC Home)
  0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, HID_REPORT_CONSUMER,
  0x15, 0x00, 0x26, 0xFF, 0x03, 0x19, 0x00, 0x2A, 0xFF, 0x03, 0x75, 0x10, 0x95, 0x01, 0x81, 0x00,
  0xC0,
  // Mouse, report 3: 5 buttons + padding; relative x, y, wheel, pan
  0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, HID_REPORT_MOUSE, 0x09, 0x01, 0xA1, 0x00,
  0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00, 0x25, 0x01, 0x95, 0x05, 0x75, 0x01, 0x81, 0x02,
  0x95, 0x01, 0x75, 0x03, 0x81, 0x01,
  0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06,
  0x05, 0x0C, 0x0A, 0x38, 0x02, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
  0xC0, 0xC0,
};
const size_t hid_report_map_len = sizeof hid_report_map;

static uint8_t lookup(char c, const char *keys, const uint8_t *usages, uint8_t flag) {
  const char *p = c ? strchr(keys, c) : NULL;
  return p ? (uint8_t)(usages[p - keys] | flag) : 0;
}

uint8_t hid_ascii_usage(char c) {
  static const uint8_t usages[] = {0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38};
  static const uint8_t digits[] = {0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27};
  if (c >= 'a' && c <= 'z') return (uint8_t)(0x04 + c - 'a');
  if (c >= 'A' && c <= 'Z') return (uint8_t)((0x04 + c - 'A') | 0x80);
  if (c >= '1' && c <= '9') return (uint8_t)(0x1E + c - '1');
  if (c == '0') return 0x27;
  if (c == '\n') return 0x28;
  if (c == '\b') return 0x2A;
  if (c == '\t') return 0x2B;
  if (c == ' ') return 0x2C;
  uint8_t u = lookup(c, "-=[]\\;'`,./", usages, 0);
  if (!u) u = lookup(c, "_+{}|:\"~<>?", usages, 0x80);
  if (!u) u = lookup(c, "!@#$%^&*()", digits, 0x80);
  return u;
}

uint8_t hid_ascii_usage_de(char c) {
  // a-x sit at the same physical key as on a US keyboard; only y and z swap places on QWERTZ.
  if (c >= 'a' && c <= 'x') return (uint8_t)(0x04 + c - 'a');
  if (c >= 'A' && c <= 'X') return (uint8_t)((0x04 + c - 'A') | 0x80);
  if (c == 'y') return 0x1D;
  if (c == 'Y') return (uint8_t)(0x1D | 0x80);
  if (c == 'z') return 0x1C;
  if (c == 'Z') return (uint8_t)(0x1C | 0x80);
  if (c >= '1' && c <= '9') return (uint8_t)(0x1E + c - '1');  // digit row: same keys as US
  if (c == '0') return 0x27;
  if (c == '\n') return 0x28;
  if (c == '\b') return 0x2A;
  if (c == '\t') return 0x2B;
  if (c == ' ') return 0x2C;
  switch ((unsigned char)c) {
    case 0xDF: return 0x2D;              // ß
    case '?':  return 0x2D | 0x80;
    case 0xFC: return 0x2F;              // ü
    case 0xDC: return 0x2F | 0x80;       // Ü
    case '+':  return 0x30;
    case '*':  return 0x30 | 0x80;
    case '#':  return 0x32;              // the extra "Non-US # and ~" key
    case '\'': return 0x32 | 0x80;
    case 0xF6: return 0x33;              // ö
    case 0xD6: return 0x33 | 0x80;       // Ö
    case 0xE4: return 0x34;              // ä
    case 0xC4: return 0x34 | 0x80;       // Ä
    case ',':  return 0x36;
    case ';':  return 0x36 | 0x80;
    case '.':  return 0x37;
    case ':':  return 0x37 | 0x80;
    case '-':  return 0x38;
    case '_':  return 0x38 | 0x80;
    case '<':  return 0x64;              // the extra "Non-US \ and |" (ISO) key
    case '>':  return 0x64 | 0x80;
    case '!':  return 0x1E | 0x80;       // shifted digit row, mostly unlike the US table
    case '"':  return 0x1F | 0x80;
    case '$':  return 0x21 | 0x80;
    case '%':  return 0x22 | 0x80;
    case '&':  return 0x23 | 0x80;
    case '/':  return 0x24 | 0x80;
    case '(':  return 0x25 | 0x80;
    case ')':  return 0x26 | 0x80;
    case '=':  return 0x27 | 0x80;
    default: return 0;  // AltGr-only (@ { } [ ] \ | ~ €) and dead keys (´ ` ^ °): not supported here
  }
}

void hid_keyboard_clear(hid_keyboard *k) { memset(k->report, 0, sizeof k->report); }

bool hid_keyboard_press(hid_keyboard *k, uint8_t code) {
  uint8_t usage;
  if (code >= 0x88) {
    usage = (uint8_t)(code - 0x88);
  } else if (code >= 0x80) {
    k->report[0] |= (uint8_t)(1 << (code - 0x80));
    return true;
  } else {
    usage = hid_ascii_usage((char)code);
    if (!usage) return false;
    if (usage & 0x80) k->report[0] |= 0x02;
    usage &= 0x7F;
  }
  for (int i = 2; i < HID_KEYBOARD_LEN; i++)
    if (k->report[i] == usage) return true;
  for (int i = 2; i < HID_KEYBOARD_LEN; i++)
    if (!k->report[i]) { k->report[i] = usage; return true; }
  return false;
}

static uint8_t clamp8(int v) { return (uint8_t)(int8_t)(v > 127 ? 127 : v < -127 ? -127 : v); }

void hid_mouse_report(uint8_t out[HID_MOUSE_LEN], uint8_t buttons, int x, int y, int wheel, int pan) {
  out[0] = buttons;
  out[1] = clamp8(x);
  out[2] = clamp8(y);
  out[3] = clamp8(wheel);
  out[4] = clamp8(pan);
}

void hid_consumer_report(uint8_t out[HID_CONSUMER_LEN], uint16_t usage) {
  out[0] = (uint8_t)usage;
  out[1] = (uint8_t)(usage >> 8);
}
