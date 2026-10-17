/* Generated UI shell for Role Runner Smoke Product */
import CommandCenterModule from './modules/command_center';
export default function App(){
  return (
    <main data-product="runner-smoke">
      <h1>Role Runner Smoke Product</h1>
      <p>Minimal blueprint driven end to end by the role runner. Uses blocks whose vendored source imports with no store configured, so the CLONER's offline import gate is a real check rather than a formality.</p>
      <CommandCenterModule />
    </main>
  )
}
