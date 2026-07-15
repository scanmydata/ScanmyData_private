    (function(){
      const SCALE_MIN = 0.96;
      const SCALE_MAX = 1.18;
      const WIDTH_MIN = 360;
      const WIDTH_MAX = 1600;
      let lastType = null;
      let lastScale = null;
      let resizeTimer = null;

      function detectDeviceType(){
        const ua = (navigator.userAgent || '').toLowerCase();
        const width = window.innerWidth || document.documentElement.clientWidth || 0;
        if (width <= 768 || /mobile|android|iphone|ipod/.test(ua)) return 'mobile';
        if (width <= 1200 || /ipad|tablet/.test(ua)) return 'tablet';
        return 'desktop';
      }

      function assignBody(type){
        if (!document.body){
          document.addEventListener('DOMContentLoaded', () => assignBody(type), { once:true });
          return;
        }
        document.body.dataset.device = type;
      }

      function computeScale(width){
        const w = Math.min(Math.max(width || WIDTH_MIN, WIDTH_MIN), WIDTH_MAX);
        const ratio = (w - WIDTH_MIN) / (WIDTH_MAX - WIDTH_MIN);
        const scale = SCALE_MAX - ratio * (SCALE_MAX - SCALE_MIN);
        return Number(scale.toFixed(3));
      }

      function applyDeviceType(){
        const type = detectDeviceType();
        const width = window.innerWidth || document.documentElement.clientWidth || screen.width || WIDTH_MAX;
        const scale = computeScale(width);
        if (type !== lastType) {
          lastType = type;
          assignBody(type);
        }
        if (lastScale !== scale) {
          lastScale = scale;
          document.documentElement.style.setProperty('--device-font-scale', String(scale));
        }
      }

      function schedule(){
        if (resizeTimer){
          clearTimeout(resizeTimer);
        }
        resizeTimer = setTimeout(applyDeviceType, 90);
      }

      if (document.readyState === 'loading'){
        document.addEventListener('DOMContentLoaded', applyDeviceType, { once:true });
      } else {
        applyDeviceType();
      }

      window.addEventListener('resize', schedule);
      window.addEventListener('orientationchange', schedule);
      if (window.ResizeObserver){
        try {
          const ro = new ResizeObserver(() => schedule());
          ro.observe(document.documentElement);
        } catch(_){}
      }
    })();
  
