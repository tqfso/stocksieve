// 本文件让普通 TypeScript 环境识别 Vue 单文件组件；组件内部由 Vue 语言工具检查。
declare module '*.vue' {
  import type { DefineComponent } from 'vue';

  const component: DefineComponent;
  export default component;
}
