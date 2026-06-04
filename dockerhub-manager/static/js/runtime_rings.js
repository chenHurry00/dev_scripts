(function (global) {
  'use strict';

  function clampPercent(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return 0;
    return Math.max(0, Math.min(100, num));
  }

  function formatFraction(current, total) {
    const currentValue = Math.max(0, Number(current || 0));
    const totalValue = Math.max(0, Number(total || 0));
    return `${Math.round(currentValue)}/${Math.round(totalValue)}`;
  }

  function gpuDeviceState(device) {
    const util = Number(device && device.utilization_gpu || 0);
    const memoryPercent = Number(device && device.memory_usage_percent || 0);
    if (util > 90 || memoryPercent > 90) return 'danger';
    if (util > 60 || memoryPercent > 60) return 'warn';
    return 'ok';
  }

  function gpuDeviceScore(device) {
    const util = Number(device && device.utilization_gpu || 0);
    const memoryPercent = Number(device && device.memory_usage_percent || 0);
    const activeBias = device && device.active ? 1000 : 0;
    return activeBias + Math.max(util, memoryPercent);
  }

  function sortGpuDevices(devices) {
    return [...(Array.isArray(devices) ? devices : [])].sort((left, right) => {
      const diff = gpuDeviceScore(right) - gpuDeviceScore(left);
      if (diff !== 0) return diff;
      return String(left && left.id || '').localeCompare(String(right && right.id || ''), 'zh-CN', { numeric: true });
    });
  }

  function setRingProgress(element, progress) {
    if (!element) return;
    element.style.setProperty('--ring-progress', `${clampPercent(progress).toFixed(1)}%`);
  }

  function animateRingProgress(container, store) {
    if (!container) return;
    const targetStore = store || {};
    const rings = container.querySelectorAll('[data-runtime-ring-key][data-runtime-ring-target]');
    rings.forEach(ring => {
      const key = String(ring.getAttribute('data-runtime-ring-key') || '').trim();
      const target = clampPercent(Number(ring.getAttribute('data-runtime-ring-target') || 0));
      const previous = key && Number.isFinite(Number(targetStore[key])) ? Number(targetStore[key]) : target;
      setRingProgress(ring, previous);
      if (Math.abs(previous - target) < 0.2) {
        setRingProgress(ring, target);
        if (key) targetStore[key] = target;
        return;
      }
      const startedAt = performance.now();
      const duration = 360;
      const step = now => {
        if (!ring.isConnected) return;
        const ratio = Math.min(1, (now - startedAt) / duration);
        const eased = 1 - Math.pow(1 - ratio, 3);
        setRingProgress(ring, previous + (target - previous) * eased);
        if (ratio < 1) {
          requestAnimationFrame(step);
        } else {
          setRingProgress(ring, target);
        }
      };
      requestAnimationFrame(step);
      if (key) targetStore[key] = target;
    });
  }

  function renderRing(metric, options) {
    const config = options || {};
    const escapeHtml = config.escapeHtml || (value => String(value || ''));
    const statePrefix = String(config.stateClassPrefix || '');
    const toggleClass = metric && metric.toggle ? ' runtime-ring-toggle' : '';
    const tag = metric && metric.toggle ? 'button' : 'div';
    const toggleAttr = metric && metric.toggle
      ? ` type="button" data-runtime-gpu-toggle="${escapeHtml(metric.serverId || '')}"`
      : '';
    const targetProgress = clampPercent(metric && metric.progress);
    const stateClass = `${statePrefix}${metric && metric.state ? metric.state : 'muted'}`;
    return `<${tag} class="runtime-ring ${stateClass}${toggleClass}"${toggleAttr} data-runtime-ring-key="${escapeHtml(metric && metric.key || '')}" data-runtime-ring-target="${targetProgress.toFixed(1)}" style="--ring-progress:0%">
      <div class="runtime-ring-visual">
        <div class="runtime-ring-core">
          <div class="runtime-ring-value">${escapeHtml(metric && metric.value || '—')}</div>
          <div class="runtime-ring-label">${escapeHtml(metric && metric.label || '')}</div>
        </div>
      </div>
      <div class="runtime-ring-subtitle">${escapeHtml(metric && metric.subtitle || '—')}</div>
    </${tag}>`;
  }

  function buildMetrics(config) {
    const runtime = config && config.runtime ? config.runtime : {};
    const keyBase = String(config && config.keyBase || 'server');
    const expanded = !!(config && config.expanded);
    const gpuLimit = Math.max(0, Number(config && config.gpuLimit || 0));
    const formatPercent = config && config.formatPercent;
    const formatBytesPair = config && config.formatBytesPair;
    const formatBytesCompact = config && config.formatBytesCompact;

    if (typeof formatPercent !== 'function' || typeof formatBytesPair !== 'function' || typeof formatBytesCompact !== 'function') {
      throw new Error('buildMetrics requires formatPercent, formatBytesPair, formatBytesCompact callbacks');
    }

    if (!runtime || (!runtime.sampled_at && !runtime.error)) {
      return [{
        key: `${keyBase}:pending`,
        value: '—',
        label: '运行状态',
        subtitle: '等待采样',
        progress: 0,
        state: 'muted'
      }];
    }

    const cpu = runtime.cpu || {};
    const memory = runtime.memory || {};
    const gpu = runtime.gpu || {};
    const storage = runtime.storage || {};
    const workspace = storage.workspace || {};
    const metrics = [
      {
        key: `${keyBase}:cpu`,
        value: formatPercent(cpu.usage_percent || 0),
        label: 'CPU',
        subtitle: `Load ${Number(cpu.load1 || 0).toFixed(1)} / ${cpu.cores || '—'}核`,
        progress: cpu.usage_percent || 0,
        state: cpu.state || 'muted'
      },
      {
        key: `${keyBase}:memory`,
        value: formatPercent(memory.usage_percent || 0),
        label: '内存',
        subtitle: formatBytesPair(memory.used_bytes || 0, memory.total_bytes || 0),
        progress: memory.usage_percent || 0,
        state: memory.state || memory.overall_state || 'muted'
      },
      {
        key: `${keyBase}:swap`,
        value: formatPercent(memory.swap_usage_percent || 0),
        label: 'SWAP',
        subtitle: Number(memory.swap_total_bytes || 0) > 0
          ? formatBytesPair(memory.swap_used_bytes || 0, memory.swap_total_bytes || 0)
          : '未启用',
        progress: Number(memory.swap_total_bytes || 0) > 0 ? (memory.swap_usage_percent || 0) : 0,
        state: memory.swap_state || 'ok'
      }
    ];

    const dockerRoot = storage.docker_root || {};
    metrics.push({
      key: `${keyBase}:docker`,
      value: formatPercent(dockerRoot.usage_percent || 0),
      label: 'Docker',
      subtitle: Number(dockerRoot.total_bytes || 0) > 0
        ? formatBytesPair(dockerRoot.used_bytes || 0, dockerRoot.total_bytes || 0)
        : '未检测到',
      progress: dockerRoot.usage_percent || 0,
      state: dockerRoot.state || 'muted'
    });

    metrics.push({
      key: `${keyBase}:workspace`,
      value: formatPercent(workspace.usage_percent || 0),
      label: '工作区',
      subtitle: Number(workspace.count || 0) > 1
        ? `${workspace.count} 挂载点 | 空闲 ${formatBytesCompact(workspace.free_bytes || 0)}`
        : (Number(workspace.total_bytes || 0) > 0 ? formatBytesPair(workspace.used_bytes || 0, workspace.total_bytes || 0) : '未配置'),
      progress: workspace.usage_percent || 0,
      state: workspace.state || 'ok'
    });

    if (Number(gpu.device_count || 0) > 0) {
      metrics.push({
        key: `${keyBase}:gpu-summary`,
        value: formatFraction(gpu.active_device_count || 0, gpu.device_count || 0),
        label: 'GPU汇总',
        subtitle: `VRAM ${formatBytesPair(gpu.used_memory_bytes || 0, gpu.total_memory_bytes || 0)}`,
        progress: Number(gpu.device_count || 0) > 0 ? (Number(gpu.active_device_count || 0) / Number(gpu.device_count || 1)) * 100 : 0,
        state: gpu.state || 'ok'
      });
      const devices = sortGpuDevices(gpu.devices || []);
      const visibleDevices = expanded ? devices : devices.slice(0, gpuLimit);
      visibleDevices.forEach(device => {
        metrics.push({
          key: `${keyBase}:gpu-device-${device.id || '?'}`,
          value: formatPercent(device.utilization_gpu || 0),
          label: `GPU-${device.id || '?'}`,
          subtitle: formatBytesPair(device.memory_used_bytes || 0, device.memory_total_bytes || 0),
          progress: Math.max(Number(device.utilization_gpu || 0), Number(device.memory_usage_percent || 0)),
          state: gpuDeviceState(device)
        });
      });
      if (!expanded && devices.length > gpuLimit) {
        metrics.push({
          key: `${keyBase}:gpu-toggle`,
          value: `+${devices.length - gpuLimit}`,
          label: 'GPU',
          subtitle: '展开全部',
          progress: 0,
          state: 'muted',
          toggle: true,
          serverId: config && config.serverId || ''
        });
      } else if (expanded && devices.length > gpuLimit) {
        metrics.push({
          key: `${keyBase}:gpu-toggle`,
          value: '收起',
          label: 'GPU',
          subtitle: `已展开 ${devices.length} 张`,
          progress: 0,
          state: 'muted',
          toggle: true,
          serverId: config && config.serverId || ''
        });
      }
    } else {
      metrics.push({
        key: `${keyBase}:gpu-empty`,
        value: '0',
        label: 'GPU',
        subtitle: '无可用设备',
        progress: 0,
        state: 'warn'
      });
    }

    return metrics;
  }

  global.RuntimeRingUtils = {
    clampPercent,
    formatFraction,
    gpuDeviceState,
    sortGpuDevices,
    setRingProgress,
    animateRingProgress,
    renderRing,
    buildMetrics,
  };
})(window);
