/* RAGFlow 权限门户 — 共享交互层(D1 Graphite)
   侧栏抽屉 / 导航激活 / 筛选 pill / 开关 / 抽屉面板 / 对话列表切换 */
(function () {
  'use strict';

  // 侧栏抽屉(移动端)
  var menuBtn = document.getElementById('menuBtn');
  var sidebar = document.getElementById('sidebar');
  if (menuBtn && sidebar) {
    menuBtn.addEventListener('click', function () { sidebar.classList.toggle('open'); });
    document.addEventListener('click', function (e) {
      if (sidebar.classList.contains('open') && !sidebar.contains(e.target) && e.target !== menuBtn) {
        sidebar.classList.remove('open');
      }
    });
  }

  // 导航激活态(同组单选)
  document.querySelectorAll('.nav-item').forEach(function (n) {
    n.addEventListener('click', function () {
      n.parentElement.querySelectorAll('.nav-item').forEach(function (x) { x.classList.remove('active'); });
      n.classList.add('active');
    });
  });

  // 筛选 pill(同组单选)
  document.querySelectorAll('[data-pill-group]').forEach(function (grp) {
    grp.querySelectorAll('.pill').forEach(function (p) {
      p.addEventListener('click', function () {
        grp.querySelectorAll('.pill').forEach(function (x) { x.classList.remove('active'); });
        p.classList.add('active');
      });
    });
  });

  // 开关
  document.querySelectorAll('.toggle').forEach(function (t) {
    t.addEventListener('click', function () { t.classList.toggle('on'); });
  });

  // 抽屉面板(data-drawer-target)
  document.querySelectorAll('[data-drawer-target]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var sel = btn.getAttribute('data-drawer-target');
      var dw = document.querySelector(sel);
      var ov = document.querySelector(sel + '-overlay');
      if (dw) dw.classList.add('open');
      if (ov) ov.classList.add('open');
      document.body.style.overflow = 'hidden';
    });
  });
  function closeDrawer() {
    document.querySelectorAll('.drawer.open').forEach(function (d) { d.classList.remove('open'); });
    document.querySelectorAll('.drawer-overlay.open').forEach(function (d) { d.classList.remove('open'); });
    document.body.style.overflow = '';
  }
  document.querySelectorAll('.drawer-overlay').forEach(function (ov) {
    ov.addEventListener('click', closeDrawer);
  });
  document.querySelectorAll('[data-drawer-close]').forEach(function (b) {
    b.addEventListener('click', closeDrawer);
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeDrawer(); });

  // 对话列表切换(分享详情页)
  document.querySelectorAll('.conv-item').forEach(function (c) {
    c.addEventListener('click', function () {
      c.parentElement.querySelectorAll('.conv-item').forEach(function (x) { x.classList.remove('active'); });
      c.classList.add('active');
    });
  });

  // Chat 输入:Enter 发送(不刷新页面),Shift+Enter 换行
  var ciForm = document.getElementById('chatInputForm');
  if (ciForm) {
    ciForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var ta = ciForm.querySelector('textarea');
      if (!ta || !ta.value.trim()) return;
      var stream = document.querySelector('.chat-stream');
      if (stream) {
        var msg = document.createElement('div');
        msg.className = 'msg user';
        msg.innerHTML = '<div class="avatar-sm">我</div><div class="mb"><div class="who">我</div><div class="mt"></div></div>';
        msg.querySelector('.mt').textContent = ta.value;
        stream.appendChild(msg);
        stream.scrollTop = stream.scrollHeight;
        ta.value = '';
      }
    });
  }

  // 分页按钮激活
  document.querySelectorAll('.pagers').forEach(function (pg) {
    pg.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function () {
        pg.querySelectorAll('button').forEach(function (x) { x.classList.remove('active'); });
        b.classList.add('active');
      });
    });
  });
})();
