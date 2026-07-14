import { ThemeProvider, useTheme } from '@/components/theme-provider';
import { ThemeEnum } from '@/constants/common';
import { render, screen, waitFor } from '@testing-library/react';
import {
  PORTAL_EMBED_THEME_STORAGE_KEY,
  RAGFLOW_THEME_STORAGE_KEY,
  getThemeProviderConfig,
} from './theme-initialization';

function ThemeValue() {
  const { theme } = useTheme();
  return <span>{theme}</span>;
}

describe('Portal embed theme initialization', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = '';
  });

  it('preserves an explicit Portal theme without changing the global RAGFlow preference', async () => {
    localStorage.setItem(PORTAL_EMBED_THEME_STORAGE_KEY, ThemeEnum.Dark);
    localStorage.setItem(RAGFLOW_THEME_STORAGE_KEY, ThemeEnum.Light);

    render(
      <ThemeProvider {...getThemeProviderConfig('?default_theme=light')}>
        <ThemeValue />
      </ThemeProvider>,
    );

    expect(screen.getByText(ThemeEnum.Dark)).toBeInTheDocument();
    await waitFor(() =>
      expect(document.documentElement).toHaveClass(ThemeEnum.Dark),
    );
    expect(localStorage.getItem(RAGFLOW_THEME_STORAGE_KEY)).toBe(
      ThemeEnum.Light,
    );
  });
});
