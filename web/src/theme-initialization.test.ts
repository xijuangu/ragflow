import { ThemeEnum } from '@/constants/common';
import {
  PORTAL_EMBED_THEME_STORAGE_KEY,
  RAGFLOW_THEME_STORAGE_KEY,
  getThemeProviderConfig,
} from './theme-initialization';

describe('getThemeProviderConfig', () => {
  it('uses a light default and isolated storage for Portal embeds', () => {
    expect(getThemeProviderConfig('?default_theme=light')).toEqual({
      defaultTheme: ThemeEnum.Light,
      storageKey: PORTAL_EMBED_THEME_STORAGE_KEY,
    });
  });

  it('keeps the existing dark default and storage outside Portal embeds', () => {
    expect(getThemeProviderConfig('')).toEqual({
      defaultTheme: ThemeEnum.Dark,
      storageKey: RAGFLOW_THEME_STORAGE_KEY,
    });
    expect(getThemeProviderConfig('?default_theme=sepia')).toEqual({
      defaultTheme: ThemeEnum.Dark,
      storageKey: RAGFLOW_THEME_STORAGE_KEY,
    });
  });

  it('accepts an explicit dark default without sharing global storage', () => {
    expect(getThemeProviderConfig('?default_theme=dark')).toEqual({
      defaultTheme: ThemeEnum.Dark,
      storageKey: PORTAL_EMBED_THEME_STORAGE_KEY,
    });
  });
});
