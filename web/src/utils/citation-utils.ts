export const normalizeCitationDigits = (text: string) => {
  if (!text) return text;
  return text.replace(/[٠-٩۰-۹]/g, (char) => {
    const code = char.charCodeAt(0);
    if (code >= 0x0660 && code <= 0x0669) {
      return String.fromCharCode(code - 0x0660 + 0x30);
    }
    if (code >= 0x06f0 && code <= 0x06f9) {
      return String.fromCharCode(code - 0x06f0 + 0x30);
    }
    return char;
  });
};

export const parseCitationIndex = (value: string) => {
  const normalized = normalizeCitationDigits(value.normalize('NFKC'));
  const markerMatch = normalized.match(/\[\s*(?:ID\s*:\s*)?(\d+)\s*\]/i);
  if (markerMatch) return Number(markerMatch[1]);
  if (/^\d+$/.test(normalized)) return Number(normalized);
  return Number.NaN;
};

export const citationMarkerReg =
  /(?:\[|［)\s*(?:ID\s*(?::|：)\s*)?([0-9\u0660-\u0669\u06F0-\u06F9]+)\s*(?:\]|］)/gi;
