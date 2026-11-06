import { describe, expect, it } from 'vitest';

import { accountFromQuery, accountFromUrl, matchOp } from '../lib/capture';

describe('matchOp', () => {
  it('reconoce la operación por nombre, sin importar el queryId', () => {
    expect(matchOp('/i/api/graphql/AbC123/SearchTimeline?variables=%7B%7D')).toBe('SearchTimeline');
    expect(matchOp('https://x.com/i/api/graphql/zzz/UserTweets')).toBe('UserTweets');
    expect(matchOp('/i/api/graphql/AbC123/HomeTimeline?x=1')).toBeNull();
  });
});

describe('accountFromQuery', () => {
  it('saca el handle de from: y lo pasa a minúsculas, como lo guarda el backend', () => {
    expect(accountFromQuery('from:NASA since:2025-01-01')).toBe('nasa');
    expect(accountFromQuery('FROM:Some_User')).toBe('some_user');
    expect(accountFromQuery('gatos lindos')).toBeNull();
  });
});

describe('accountFromUrl', () => {
  it('lee la cuenta de las URLs relativas que pide x.com', () => {
    const variables = JSON.stringify({ rawQuery: 'from:NASA since:2025-01-01 until:2025-02-01' });
    const url = `/i/api/graphql/AbC/SearchTimeline?variables=${encodeURIComponent(variables)}`;
    expect(accountFromUrl(url)).toBe('nasa');
  });
  it('devuelve null si no hay búsqueda from: o la URL no se puede leer', () => {
    const userTweets = `/i/api/graphql/AbC/UserTweets?variables=${encodeURIComponent('{"userId":"1"}')}`;
    expect(accountFromUrl(userTweets)).toBeNull();
    expect(accountFromUrl('/i/api/graphql/AbC/SearchTimeline?variables=no-es-json')).toBeNull();
    expect(accountFromUrl('/i/api/graphql/AbC/SearchTimeline')).toBeNull();
  });
});
