import MarkdownIt from 'markdown-it'

export const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

export function renderMarkdown(text: string): string {
  if (!text) return ''
  return md.render(text)
}
