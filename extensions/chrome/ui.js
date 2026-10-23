export async function send(type, fields = {}) {
  const response = await chrome.runtime.sendMessage({type, ...fields});
  if (!response?.ok) throw new Error(response?.error || "Could not reach the extension service worker.");
  return response.data;
}

export function showError(element, error) {
  element.textContent = error.message === "Failed to fetch" ? "Cannot reach local Imprint. Start the backend and check the address in settings." : error.message;
}
