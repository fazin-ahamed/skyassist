const dubai = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Dubai", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

export function hhmm(iso?: string | null): string {
  return iso ? dubai.format(new Date(iso)) : "";
}

export function minutesBetween(fromIso: string, toIso: string): number {
  return Math.floor((new Date(toIso).getTime() - new Date(fromIso).getTime()) / 60000);
}

export function statusKey(label: string): string {
  return "status." + label.toLowerCase().replace(/ /g, "_");
}
