// PSJBA Focus Tracker — active tab 감지 → 모든 탭 content script에 브로드캐스트
// content script가 document.documentElement에 data-psjba-active 속성으로 기록

function broadcast(activeTabId) {
  chrome.tabs.query({}, (tabs) => {
    for (const t of tabs) {
      const isActive = t.id === activeTabId;
      chrome.tabs.sendMessage(
        t.id,
        { type: "SET_ACTIVE", value: isActive ? "true" : "false" }
      ).catch(() => {}); // content script 없는 탭(chrome:// 등)은 무시
    }
  });
}

chrome.tabs.onActivated.addListener((activeInfo) => {
  broadcast(activeInfo.tabId);
});

// 페이지 새로고침/네비게이션 후에도 활성 마커 재설정
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "complete") {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs && tabs.length > 0) broadcast(tabs[0].id);
    });
  }
});

// 최초 로드 시 현재 활성 탭 기록
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  if (tabs && tabs.length > 0) broadcast(tabs[0].id);
});