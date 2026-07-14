import { citationMarkerReg, parseCitationIndex } from '../citation-utils';

describe('citation marker compatibility', () => {
  it('recognizes model-produced spacing and full-width punctuation variants', () => {
    const answer =
      '法定节假日[ID:0]、带薪年休假[ID: 1]以及限制延长工作时间［ID：3］';

    const markers = Array.from(answer.matchAll(citationMarkerReg));

    expect(markers.map((marker) => marker[0])).toEqual([
      '[ID:0]',
      '[ID: 1]',
      '［ID：3］',
    ]);
    expect(markers.map((marker) => parseCitationIndex(marker[0]))).toEqual([
      0, 1, 3,
    ]);
  });
});
