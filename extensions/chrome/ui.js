export async function send(type, fields = {}) {
  const response = await chrome.runtime.sendMessage({type, ...fields});
  if (!response?.ok) throw new Error(response?.error || "Could not reach the extension service worker.");
  return response.data;
}

// Fetch failures cross the service worker as bare messages.
export function unreachable(message = "") {
  return /Failed to fetch|NetworkError|timed out|TimeoutError/i.test(message);
}

export function friendlyMessage(message = "") {
  if (/timed out|TimeoutError/i.test(message)) return "Imprint didn’t answer in time. Check that it is running, then try again.";
  if (unreachable(message)) return "Can’t reach Imprint on this computer. Open the Imprint app, then try again.";
  return message;
}

export function showError(element, error) {
  element.classList.add("error");
  element.classList.remove("busy");
  element.textContent = friendlyMessage(error.message);
}
