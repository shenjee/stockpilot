export interface SerialTaskQueue<Input, Output> {
  enqueue(value: Input): Promise<Output>;
}

export function createSerialTaskQueue<Input, Output>(
  run: (value: Input) => Promise<Output>,
): SerialTaskQueue<Input, Output>;
