import { useState } from 'react';
import { Button, Input } from 'antd';
import type { ToolInfo } from '../api/client';

export function ToolInvocation({ tool, disabled, invoke }: {
  tool: ToolInfo; disabled: boolean; invoke: (arguments_: Record<string, unknown>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const properties = Object.entries(tool.input_schema?.properties ?? {});
  const required = tool.input_schema?.required ?? [];
  const unsupported = properties.length > 8 || properties.some(([, property]) =>
    !['string', 'number', 'integer'].includes(property.type ?? '')) ||
    required.some((key) => !properties.some(([name]) => name === key));
  const arguments_: Record<string, unknown> = {};
  let invalid = unsupported;
  for (const [name, property] of properties) {
    const value = values[name] ?? String(property.default ?? '');
    if (!value.trim()) { if (required.includes(name)) invalid = true; continue; }
    if (property.type === 'string') {
      arguments_[name] = value.trim();
      invalid ||= value.trim().length < (property.minLength ?? 0) || value.trim().length > (property.maxLength ?? 1000);
    } else {
      const number = Number(value);
      invalid ||= !Number.isFinite(number) || property.type === 'integer' && !Number.isSafeInteger(number) ||
        number < (property.minimum ?? -Infinity) || number > (property.maximum ?? Infinity);
      arguments_[name] = number;
    }
  }
  return <>
    {unsupported ? <p className="muted">此工具的参数格式尚未接入手动表单，可通过已启用的自动任务补参。</p> :
      <div className="tool-inputs">{properties.map(([name, property]) => <label key={name}>
        {property.title ?? name}{required.includes(name) ? ' *' : ''}
        <Input aria-label={property.title ?? name} value={values[name] ?? String(property.default ?? '')}
          disabled={disabled} maxLength={Math.min(property.maxLength ?? 1000, 1000)}
          inputMode={property.type === 'string' ? 'text' : 'decimal'}
          onChange={(event) => setValues((old) => ({ ...old, [name]: event.target.value }))} />
      </label>)}</div>}
    <Button size="small" aria-label={`调用工具：${tool.title}`} disabled={disabled || invalid}
      onClick={() => invoke(arguments_)}>调用工具</Button>
  </>;
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' &&
  !Array.isArray(value) ? value as Record<string, unknown> : {};
const measure = (value: unknown) => {
  const item = record(value);
  return typeof item.value === 'number' && Number.isFinite(item.value) && typeof item.unit === 'string' ?
    `${item.value} ${item.unit}` : '未提供';
};
const text = (value: unknown) => typeof value === 'string' ? value : '';

export function WeatherResult({ result }: { result: Record<string, unknown> }) {
  const current = record(result.current);
  const location = record(result.location);
  const queriedAt = text(result.queried_at);
  const candidates = Array.isArray(result.candidates) ? result.candidates : [];
  const days = Array.isArray(result.days) ? result.days : [];
  return <section className="tool-result" aria-label="和风天气工具结果">
    <strong>和风天气 · 外部工具结果</strong>
    {typeof location.latitude === 'number' && typeof location.longitude === 'number' &&
      <p>查询坐标：纬度 {location.latitude}，经度 {location.longitude}</p>}
    {result.kind === 'current' && <>
      <p>{text(current.condition)} · {measure(current.temperature)}</p>
      {typeof current.humidity_percent === 'number' && <p>相对湿度：{current.humidity_percent}%</p>}
      {current.feels_like != null && <p>体感温度：{measure(current.feels_like)}</p>}
      {current.wind_speed != null && <p>风速：{measure(current.wind_speed)}</p>}
    </>}
    {result.kind === 'city_search' && <>
      <ul>{candidates.map((value, index) => {
        const city = record(value);
        return <li key={index}>{text(city.name)} · {text(city.adm1)} {text(city.adm2)} ·
          纬度 {String(city.latitude ?? '')}，经度 {String(city.longitude ?? '')}</li>;
      })}</ul>
      {result.requires_selection === true && <p>有多个候选地点，请确认行政区后再查询天气。</p>}
    </>}
    {result.kind === 'forecast' && days.map((value, index) => {
      const day = record(value); const daytime = record(day.daytime); const nighttime = record(day.nighttime);
      return <div key={index}><p>预报区间（UTC）：{text(day.forecast_start_time)} 至 {text(day.forecast_end_time)}</p>
        <p>温度：{measure(day.temperature_min)} 至 {measure(day.temperature_max)}</p>
        <p>白天：{text(daytime.condition)} · 夜间：{text(nighttime.condition)}</p>
        {typeof daytime.precipitation_probability_percent === 'number' &&
          <p>白天降水概率：{daytime.precipitation_probability_percent}%</p>}</div>;
    })}
    {queriedAt && Number.isFinite(Date.parse(queriedAt)) && <p>查询时间：<time dateTime={queriedAt}>
      {new Date(queriedAt).toLocaleString('zh-CN')}</time>（不代表供应商观测时间）</p>}
    {Array.isArray(result.attributions) && <div>数据归属：{result.attributions.filter((item) =>
      typeof item === 'string').map((item, index) => <p key={index}>{String(item)}</p>)}</div>}
    {Array.isArray(result.licenses) && result.licenses.length > 0 && <div>数据许可：{result.licenses.filter((item) =>
      typeof item === 'string').map((item, index) => <p key={index}>{String(item)}</p>)}</div>}
  </section>;
}

export function toolFailure(code: string): string {
  return ({
    weather_not_configured: '天气未配置，请在后端配置 API Host 和 API Key 并显式启用。',
    weather_auth_failed: '天气认证失败，请检查后端 API Key。',
    weather_access_denied: '天气访问被拒绝，请检查账户额度、接口权限、API Host 或请求限制。',
    weather_rate_limited: '天气请求已限流，请稍后主动重试并检查供应商额度。',
    weather_timeout: '天气服务超时，请稍后主动重试。',
    weather_unavailable: '天气服务连接失败或暂不可用，请检查网络和后端 API Host。',
    weather_parameters_rejected: '天气参数被供应商拒绝，请核对城市、坐标和预报天数。',
    weather_location_not_found: '未找到地点，请补充城市或上级行政区。',
    weather_endpoint_unavailable: '天气接口不可用，请检查 API Host 和账户支持的接口。',
    weather_redirect_forbidden: '天气服务返回了跳转，已阻止转发凭证，请核对 API Host。',
    weather_result_invalid: '天气返回格式不符合当前协议，未保存或展示未经验证的数据。',
  } as Record<string, string>)[code] ?? '工具调用未完成，请核对调用记录与服务状态。';
}
