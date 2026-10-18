(() => {
  const message = document.getElementById('startup-message');
  let failures = 0;
  function selectedRoute() {
    const route = (location.hash.slice(1) || 'home').split('/')[0];
    document.querySelectorAll('[data-nav]').forEach(link => link.classList.toggle('active', link.dataset.nav === route));
  }
  async function poll() {
    try {
      const response = await fetch('/api/startup', {cache: 'no-store'});
      if (!response.ok) throw new Error('startup_unavailable');
      const status = await response.json();
      failures = 0;
      if (status.state === 'ready') {
        const target = new URL(status.url);
        if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1' || !target.port || target.username || target.password) throw new Error('invalid_target');
        target.hash = location.hash || '#home';
        location.replace(target.href);
        return;
      }
      if (status.state === 'failed') {
        message.classList.add('error');
        message.textContent = '자료 준비에 실패했습니다. 실행 로그를 확인한 뒤 대시보드를 다시 열어 주세요.';
        return;
      }
    } catch (_) {
      failures += 1;
      if (failures >= 4) {
        message.classList.add('error');
        message.textContent = '로컬 서버와 연결할 수 없습니다. 대시보드를 다시 열어 주세요.';
        return;
      }
    }
    setTimeout(poll, 500);
  }
  selectedRoute();
  addEventListener('hashchange', selectedRoute);
  poll();
})();
