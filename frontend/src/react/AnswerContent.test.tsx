import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { AnswerContent } from './AnswerContent';

afterEach(cleanup);

it('renders aligned tables, escaped pipes and code cells without changing numeric claims', () => {
  const text = '| 指标 | 数值 | 说明 |\n| :--- | ---: | :---: |\n' +
    '| 湿度 | 70% → 75% | 增加 **5 个百分点** |\n' +
    '| 编号 | `KX-17` | A\\|B 与 `x|y` |';
  render(<AnswerContent text={text} />);
  const table = screen.getByRole('table', { name: '回答表格' });
  expect(within(table).getAllByRole('row')).toHaveLength(3);
  expect(within(table).getByRole('cell', { name: '70% → 75%' }).style.textAlign).toBe('right');
  expect(within(table).getByRole('cell', { name: '增加 5 个百分点' }).style.textAlign).toBe('center');
  expect(within(table).getByRole('cell', { name: 'A|B 与 x|y' })).toBeTruthy();
  expect(table.querySelector('strong')?.textContent).toBe('5 个百分点');
  expect(table.querySelector('th')?.scope).toBe('col');
  expect(table.closest('[role="region"]')?.getAttribute('tabindex')).toBe('0');
});

it('preserves malformed table rows as readable text instead of dropping their values', () => {
  const text = '项目 | 数值\n--- | ---\n温度 | 42 摄氏度\n异常行 | 99 | 不能丢失\n\n结束。';
  render(<AnswerContent text={text} />);
  expect(screen.getByRole('cell', { name: '42 摄氏度' })).toBeTruthy();
  expect(screen.getByText('异常行 | 99 | 不能丢失')).toBeTruthy();
  expect(screen.getByText('结束。')).toBeTruthy();
});

it('leaves invalid separators and ordinary vertical bars as plain paragraphs', () => {
  render(<AnswerContent text={'A | B\n-- | --\n不是表格\n\n逻辑 a | b。'} />);
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.getByText('逻辑 a | b。')).toBeTruthy();
});

it('displays HTML, scripts, images, URL schemes and links as inert text in paragraphs and cells', () => {
  const text = '<script>window.bad=true</script>\n<img src="https://example.invalid/pixel" onerror="bad()">\n' +
    '[点击](javascript:alert(1)) ![追踪](https://example.invalid/pixel) <https://example.invalid>\n\n' +
    '| 项目 | 内容 |\n| --- | --- |\n| HTML | <iframe src="https://example.invalid"></iframe> |';
  const view = render(<AnswerContent text={text} />);
  expect(view.container.querySelectorAll('a,img,script,iframe,video,audio,object,embed')).toHaveLength(0);
  expect(view.container.textContent).toContain('<script>window.bad=true</script>');
  expect(view.container.textContent).toContain('[点击](javascript:alert(1))');
  expect(screen.getByRole('cell', { name: '<iframe src="https://example.invalid"></iframe>' })).toBeTruthy();
});

it('renders code blocks literally without interpreting tables or HTML within them', () => {
  const view = render(<AnswerContent text={'```html\n| A | B |\n| --- | --- |\n<img src=x>\n```\n\n**结论**：仅是示例。'} />);
  expect(screen.queryByRole('table')).toBeNull();
  expect(view.container.querySelector('img')).toBeNull();
  expect(view.container.querySelector('pre code')?.textContent).toBe('| A | B |\n| --- | --- |\n<img src=x>');
  expect(view.container.querySelector('strong')?.textContent).toBe('结论');
});

it('retains headings, simple lists, paragraphs and explicit line breaks', () => {
  const view = render(<AnswerContent text={'### 结论\n\n首行\n次行\n\n- **温度**达标\n- 湿度达标\n\n3. 先停机\n4. 再断电'} />);
  expect(screen.getByRole('heading', { name: '结论' })).toBeTruthy();
  expect(view.container.querySelector('p')?.textContent).toBe('首行\n次行');
  expect(screen.getAllByRole('list')).toHaveLength(2);
  expect(screen.getAllByRole('listitem')).toHaveLength(4);
  expect(view.container.querySelector('ol')?.start).toBe(3);
});

it('does not remove literal heading hashes or underscores inside identifiers', () => {
  render(<AnswerContent text={'## C#\n\n编号 QL_SPEC_V2 与 AB__CD__EF 保持原样。'} />);
  expect(screen.getByRole('heading', { name: 'C#' })).toBeTruthy();
  expect(screen.getByText('编号 QL_SPEC_V2 与 AB__CD__EF 保持原样。')).toBeTruthy();
});

it('handles a large whitespace-heavy model heading without quadratic backtracking', () => {
  const title = `a${' '.repeat(90_000)}x`;
  const started = performance.now();
  const view = render(<AnswerContent text={`## ${title}`} />);
  expect(view.container.querySelector('h3')?.textContent).toBe(title);
  expect(performance.now() - started).toBeLessThan(2_000);
});

it.each(['x'.repeat(100_001), `${'原文\n'.repeat(2_001)}结尾`])('keeps an oversized historic answer complete using a plain-text fallback', (text) => {
  const view = render(<AnswerContent text={text} />);
  expect(view.container.textContent).toBe(text);
  expect(view.container.querySelectorAll('p')).toHaveLength(1);
});
