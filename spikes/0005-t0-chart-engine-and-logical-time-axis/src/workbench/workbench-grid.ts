/**
 * 三栏工作台布局
 * - 5 分钟区 + 分时区 + 行情栏
 * - 三种布局状态：主图优先(64/36)、左右各半(50/50)、隐藏分时
 * - 行情栏固定约 280px
 * - 布局切换保持图表缩放、拖动位置和图层开关
 *
 * 参见 docs/t0assistant/ui_layout_spec.md 第 5 节"三种布局状态"。
 */

export type WorkbenchLayout = 'main-priority' | 'half-half' | 'hide-time';

export interface WorkbenchConfig {
  container: HTMLElement;
  fiveMinuteArea: HTMLElement; // 5 分钟图组容器
  timeSharingArea: HTMLElement; // 1 分钟图组容器
  quoteArea: HTMLElement; // 行情栏容器
}

const QUOTE_BAR_WIDTH = 280; // 行情栏固定宽度（px），见 ui_layout_spec 4.1

export class WorkbenchGrid {
  private container: HTMLElement;
  private fiveMinuteArea: HTMLElement;
  private timeSharingArea: HTMLElement;
  private quoteArea: HTMLElement;
  private layout: WorkbenchLayout = 'main-priority';

  // 每个区域在隐藏前保存的状态，用于恢复
  private savedTimeSharingFlex: string = '';

  constructor(config: WorkbenchConfig) {
    this.container = config.container;
    this.fiveMinuteArea = config.fiveMinuteArea;
    this.timeSharingArea = config.timeSharingArea;
    this.quoteArea = config.quoteArea;

    this.applyContainerStyle();
    this.applyAreaStyles();
    this.applyLayout(this.layout);
  }

  private applyContainerStyle() {
    this.container.style.display = 'grid';
    this.container.style.gridTemplateRows = '1fr';
    this.container.style.width = '100%';
    this.container.style.height = '100%';
  }

  private applyAreaStyles() {
    // 行情栏固定宽度，不参与比例分配
    this.quoteArea.style.width = `${QUOTE_BAR_WIDTH}px`;
    this.quoteArea.style.minWidth = `${QUOTE_BAR_WIDTH}px`;
    this.quoteArea.style.overflow = 'auto';
    this.quoteArea.style.padding = '8px';
    this.quoteArea.style.boxSizing = 'border-box';

    // 5 分钟区和分时区都用 flex column，内部放各自的图表组
    [this.fiveMinuteArea, this.timeSharingArea].forEach(area => {
      area.style.display = 'flex';
      area.style.flexDirection = 'column';
      area.style.overflow = 'hidden';
      area.style.minWidth = '0';
    });
  }

  private applyLayout(layout: WorkbenchLayout) {
    this.layout = layout;

    switch (layout) {
      case 'main-priority':
        // 5 分钟区 64% / 分时区 36% / 行情栏 280px
        this.container.style.gridTemplateColumns = `1fr 1fr ${QUOTE_BAR_WIDTH}px`;
        this.fiveMinuteArea.style.flex = '64';
        this.timeSharingArea.style.flex = '36';
        this.timeSharingArea.style.display = 'flex';
        break;

      case 'half-half':
        // 5 分钟区 50% / 分时区 50% / 行情栏 280px
        this.container.style.gridTemplateColumns = `1fr 1fr ${QUOTE_BAR_WIDTH}px`;
        this.fiveMinuteArea.style.flex = '1';
        this.timeSharingArea.style.flex = '1';
        this.timeSharingArea.style.display = 'flex';
        break;

      case 'hide-time':
        // 5 分钟区使用行情栏之外的全部空间 / 分时区隐藏 / 行情栏 280px
        this.container.style.gridTemplateColumns = `1fr 0 ${QUOTE_BAR_WIDTH}px`;
        this.fiveMinuteArea.style.flex = '1';
        this.timeSharingArea.style.flex = '0';
        this.timeSharingArea.style.display = 'none';
        break;
    }
  }

  // 切换布局，保持图表内部状态不被重置
  public setLayout(layout: WorkbenchLayout) {
    // 保存分时区在被隐藏前的状态，用于恢复
    if (this.layout === 'hide-time' && layout !== 'hide-time') {
      this.timeSharingArea.style.display = 'flex';
      if (this.savedTimeSharingFlex) {
        this.timeSharingArea.style.flex = this.savedTimeSharingFlex;
      }
    } else if (layout === 'hide-time') {
      this.savedTimeSharingFlex = this.timeSharingArea.style.flex;
    }

    this.applyLayout(layout);
  }

  public getLayout(): WorkbenchLayout {
    return this.layout;
  }

  // 从隐藏分时恢复时，恢复隐藏前的可见时间范围和图表状态
  // 注意：实际图表实例的状态保持由图表组自己负责，这里只负责 DOM 可见性
  public restoreTimeSharingArea() {
    if (this.layout === 'hide-time') {
      this.setLayout('main-priority');
    }
  }
}
