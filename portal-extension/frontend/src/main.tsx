import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { AuthProvider } from './auth/AuthContext';
import './styles.css';

// 生产部署:前端跑在 /portal/ 子路径,Router basename 需匹配,否则路由解析错位。
const routerBasename = import.meta.env.PROD ? '/portal' : '/';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter
      basename={routerBasename}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
