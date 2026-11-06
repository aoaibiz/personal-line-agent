/** Preserve non-text Note content; never use SDK updatePost's empty defaults. */
export function noteUpdateBody(home, postId, contents, text) {
  if (!contents || Array.isArray(contents) || typeof contents !== 'object') throw Error('input_invalid');
  return {postInfo:{postId,editableContents:['ALL'],readPermission:{homeID:home}},contents:{...contents,text,textMeta:[],sticonMetas:[]}};
}
