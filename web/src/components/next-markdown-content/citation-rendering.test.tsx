import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

jest.mock('../svg-icon', () => ({
  __esModule: true,
  default: () => <span data-testid="svg-icon" />,
}));

jest.mock('@/components/image', () => ({
  __esModule: true,
  default: () => <span data-testid="reference-image" />,
}));

jest.mock('@/constants/markdown-remark-plugins', () => ({
  MarkdownRemarkPlugins: [],
}));

jest.mock('react-markdown', () => ({
  __esModule: true,
  default: ({ children, components }: any) => {
    const Typography = components['custom-typography'];
    return <Typography>{children}</Typography>;
  },
  defaultUrlTransform: (value: string) => value,
}));

jest.mock('rehype-katex', () => jest.fn());
jest.mock('rehype-raw', () => jest.fn());
jest.mock('unist-util-visit-parents', () => ({ visitParents: jest.fn() }));

jest.mock('@/hooks/use-document-request', () => ({
  useFetchDocumentThumbnailsByIds: () => ({
    data: {},
    setDocumentIds: jest.fn(),
  }),
}));

// Jest must register the Vite-only dependency mocks before loading this module.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const MarkdownContent = require('.').default;

describe('MarkdownContent citations', () => {
  it('renders a compatible citation marker as an interactive reference', async () => {
    const user = userEvent.setup();
    render(
      <MarkdownContent
        loading={false}
        content="法定节假日休假［ID：0］"
        reference={
          {
            chunks: {
              0: {
                id: 'chunk-1',
                content: '全体公民放假的节日包括元旦、春节等。',
                document_id: 'doc-1',
              },
            },
            doc_aggs: {
              'doc-1': {
                doc_id: 'doc-1',
                doc_name: '劳动法.pdf',
              },
            },
          } as any
        }
      />,
    );

    const citation = screen.getByText('Fig. 1');
    expect(citation).toBeInTheDocument();
    expect(screen.queryByText('［ID：0］')).not.toBeInTheDocument();

    await user.hover(citation);
    expect(
      await screen.findByText('全体公民放假的节日包括元旦、春节等。'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '劳动法.pdf' }),
    ).toBeInTheDocument();
  });
});
