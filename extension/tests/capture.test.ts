import { describe, expect, it } from 'vitest';

import { accountFromQuery, accountFromUrl, matchOp, screenNameFromUrl } from '../lib/capture';

describe('matchOp', () => {
  it('reconoce la operación por nombre, sin importar el queryId', () => {
    expect(matchOp('/i/api/graphql/AbC123/SearchTimeline?variables=%7B%7D')).toBe('SearchTimeline');
    expect(matchOp('https://x.com/i/api/graphql/zzz/UserTweets')).toBe('UserTweets');
    expect(matchOp('/i/api/graphql/AbC123/HomeTimeline?x=1')).toBeNull();
    expect(matchOp('/i/api/graphql/Q/UserByScreenName?variables=%7B%7D')).toBe('UserByScreenName');
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

describe('screenNameFromUrl', () => {
  it('lee el handle por el que pregunta UserByScreenName, en minúsculas', () => {
    const variables = JSON.stringify({ screen_name: 'TayandYou', withGrokTranslatedBio: false });
    const url = `/i/api/graphql/Q/UserByScreenName?variables=${encodeURIComponent(variables)}`;
    expect(screenNameFromUrl(url)).toBe('tayandyou');
  });
  it('devuelve null si la URL no trae el handle', () => {
    expect(screenNameFromUrl('/i/api/graphql/Q/UserByScreenName')).toBeNull();
    expect(screenNameFromUrl('/i/api/graphql/Q/UserByScreenName?variables=%7B%7D')).toBeNull();
    expect(screenNameFromUrl('/i/api/graphql/Q/UserByScreenName?variables=no')).toBeNull();
  });
});
