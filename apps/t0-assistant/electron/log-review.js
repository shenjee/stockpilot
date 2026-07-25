const content = document.querySelector("#content");

window.stockpilotLogReview
  .read()
  .then((log) => {
    content.textContent = log || "暂无技术日志。";
    content.scrollTop = content.scrollHeight;
  })
  .catch(() => {
    content.textContent = "日志暂时无法读取。";
  });
