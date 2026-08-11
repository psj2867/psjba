// content script — background에서 SET_ACTIVE 메시지를 받아 DOM에 표시
// page.evaluate("document.documentElement.getAttribute('data-psjba-active')")
// 로 파이썬에서 읽어간다.

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "SET_ACTIVE") {
    document.documentElement.setAttribute("data-psjba-active", msg.value);
  }
});