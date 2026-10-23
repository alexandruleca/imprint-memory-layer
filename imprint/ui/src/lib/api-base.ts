/** Static exports use their own server/port unless development explicitly overrides it. */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
