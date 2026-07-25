export async function retryDesktopService(serviceHost, gateway) {
  if (serviceHost.state === "ready") {
    const connection = serviceHost.connectionInfo();
    if (!connection) {
      throw new Error("Ready service is missing connection information");
    }
    gateway.start(connection);
    return serviceHost.rendererStatus("本地服务已就绪，正在重连事件通道");
  }
  return serviceHost.start();
}
