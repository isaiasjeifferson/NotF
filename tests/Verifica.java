import javax.xml.crypto.dsig.*; import javax.xml.crypto.dsig.dom.DOMValidateContext;
import javax.xml.parsers.*; import org.w3c.dom.*; import java.io.*; import java.security.cert.*;
public class Verifica { public static void main(String[] a) throws Exception {
  DocumentBuilderFactory f=DocumentBuilderFactory.newInstance(); f.setNamespaceAware(true);
  Document d=f.newDocumentBuilder().parse(new File(a[0]));
  NodeList inf=d.getElementsByTagNameNS("*","infDPS"); if(inf.getLength()==0) inf=d.getElementsByTagNameNS("*","infPedReg");
  ((Element)inf.item(0)).setIdAttribute("Id",true);
  Element sig=(Element)d.getElementsByTagNameNS(XMLSignature.XMLNS,"Signature").item(0);
  String b64=sig.getElementsByTagNameNS(XMLSignature.XMLNS,"X509Certificate").item(0).getTextContent().trim();
  X509Certificate c=(X509Certificate)CertificateFactory.getInstance("X.509").generateCertificate(new ByteArrayInputStream(java.util.Base64.getMimeDecoder().decode(b64)));
  DOMValidateContext ctx=new DOMValidateContext(c.getPublicKey(),sig); ctx.setProperty("org.jcp.xml.dsig.secureValidation",Boolean.FALSE);
  XMLSignature s=XMLSignatureFactory.getInstance("DOM").unmarshalXMLSignature(ctx);
  boolean ok=s.validate(ctx); boolean ref=((Reference)s.getSignedInfo().getReferences().get(0)).validate(ctx);
  System.out.println("assinatura="+s.getSignatureValue().validate(ctx)+" referencia(digest)="+ref+" => "+(ok?"VALIDA":"INVALIDA"));
}}
