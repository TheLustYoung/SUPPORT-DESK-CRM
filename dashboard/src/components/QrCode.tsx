import qrcode from 'qrcode-generator';

/** The two-factor setup link as a QR code to scan with an authenticator app (rendered locally, nothing is sent anywhere). */
export function QrCode({text}: {text: string}) {
  const qr = qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  return <div className="qr" dangerouslySetInnerHTML={{__html: qr.createSvgTag({cellSize: 5, margin: 3, scalable: true})}}/>;
}
