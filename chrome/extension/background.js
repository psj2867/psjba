// PSJBA Focus Tracker — active tab 감지 → chrome.storage.session에 기록
// CDP Extensions.getStorageItems로 파이썬에서 읽어간다.

chrome.tabs.onActivated.addListener((activeInfo) => {
  chrome.tabs.get(activeInfo.tabId, (tab) => {
    if (chrome.runtime.lastError) return;
    chrome.storage.session.set({
      activeTabId: activeInfo.tabId,
      activeTabUrl: tab.url,
      activeTabTitle: tab.title,
    });
  });
});

// 최초 연결 시에도 현재 활성 탭 기록
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  if (tabs && tabs.length > 0) {
    chrome.storage.session.set({
      activeTabId: tabs[0].id,
      activeTabUrl: tabs[0].url,
      activeTabTitle: tabs[0].title,
    });
  }
});