// Sidebar toggle
document.addEventListener('DOMContentLoaded', function () {
  const toggle = document.getElementById('sidebarToggle');
  const sidebar = document.getElementById('sidebar');
  const main = document.getElementById('main-content');

  if (toggle && sidebar) {
    toggle.addEventListener('click', function () {
      const isMobile = window.innerWidth <= 768;
      if (isMobile) {
        sidebar.classList.toggle('open');
      } else {
        const collapsed = sidebar.style.width === '0px';
        sidebar.style.width = collapsed ? '240px' : '0px';
        sidebar.style.overflow = collapsed ? '' : 'hidden';
        if (main) main.style.marginLeft = collapsed ? '240px' : '0px';
      }
    });
  }

  // Auto-dismiss alerts after 5 seconds
  document.querySelectorAll('.alert.alert-success, .alert.alert-info').forEach(function (el) {
    setTimeout(function () {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(el);
      bsAlert.close();
    }, 5000);
  });
});
