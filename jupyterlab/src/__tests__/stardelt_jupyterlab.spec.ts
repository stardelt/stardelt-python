/**
 * The catalog tree's one pure piece: the query a double-clicked table starts.
 */

import { sqlSnippet } from '../catalog';

describe('@stardelt/jupyterlab', () => {
  it('should be tested', () => {
    expect(1 + 1).toEqual(2);
  });

  it('starts a query of a hundred rows for a table', () => {
    expect(sqlSnippet('mobility', 'gold', 'kpis')).toEqual(
      '%%sql\nSELECT * FROM "mobility"."gold"."kpis" LIMIT 100'
    );
  });

  it('doubles a quote inside a name', () => {
    expect(sqlSnippet('c', 's', 'we"ird')).toEqual(
      '%%sql\nSELECT * FROM "c"."s"."we""ird" LIMIT 100'
    );
  });
});
