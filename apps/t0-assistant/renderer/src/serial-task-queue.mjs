export function createSerialTaskQueue(run) {
  if (typeof run !== "function") {
    throw new TypeError("Serial task queue requires a runner");
  }
  let tail = Promise.resolve();
  return Object.freeze({
    enqueue(value) {
      const task = tail.catch(() => undefined).then(() => run(value));
      tail = task.then(
        () => undefined,
        () => undefined,
      );
      return task;
    },
  });
}
