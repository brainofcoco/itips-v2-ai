interface Props {
  value: unknown;
  collapsed?: boolean;
}

export default function JsonView({ value }: Props) {
  return (
    <pre className="json-view">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
